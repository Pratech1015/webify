#!/usr/bin/env node
/**
 * Netlify Functions runtime for Webify — a drop-in, self-hosted replacement.
 *
 * Netlify deploys each file in the functions directory as a serverless
 * endpoint at /.netlify/functions/<basename>. A self-hosted Webify site
 * (systemd + `next start` / http.server etc.) has no runtime to run those
 * handlers, which is why `/.netlify/functions/api` 404s on a self-hosted app.
 *
 * This gateway restores that behaviour with zero changes to the deployed app:
 *
 *   * It listens on the service's public port and serves two kinds of traffic:
 *       1. /.netlify/functions/*  -> invokes the actual Lambda-style handler
 *       2. everything else        -> reverse-proxied to the real site
 *     so a frontend that calls `/.netlify/functions/api` on the *same origin*
 *     keeps working unchanged.
 *   * Handlers use the Netlify / AWS Lambda signature:
 *
 *        exports.handler = async (event, context) => ({
 *            statusCode: 200, headers: {}, body: "hello"
 *        });
 *
 *     CommonJS (.js/.cjs) and ESM (.mjs) are both supported, as is the
 *     `@netlify/functions` `Handler` wrapper (it exports `handler` too).
 *
 * Environment:
 *   WEBIFY_FUNCTION_DIR  - absolute path to the functions directory
 *   WEBIFY_SITE_URL      - http://127.0.0.1:<app_port> the real app runs on
 *   WEBIFY_FUNCTIONS_PORT- port to listen on
 *   WEBIFY_BIND          - bind address (default 0.0.0.0)
 *
 * Running: node webify/functions_server.js
 */
"use strict";

const http = require("http");
const path = require("path");
const { pathToFileURL } = require("url");

const FUNCTIONS_PREFIX = "/.netlify/functions";
const BIND = process.env.WEBIFY_BIND || "0.0.0.0";
const PORT = Number(process.env.WEBIFY_FUNCTIONS_PORT || 8083);
const FUNCTION_DIR = process.env.WEBIFY_FUNCTION_DIR || "";
const SITE_URL = (process.env.WEBIFY_SITE_URL || "http://127.0.0.1:3000").replace(/\/$/, "");

const FUNCTION_EXTS = [".mjs", ".js", ".cjs"];

/** Load a function module and return its `handler` callable. */
function loadHandler(file) {
  if (file.endsWith(".mjs")) {
    // ESM: dynamic import
    return import(pathToFileURL(file).href).then((mod) => {
      let handler = mod.handler || mod.default;
      if (typeof handler !== "function") {
        throw new Error(`Function ${path.basename(file)} has no callable 'handler'.`);
      }
      return handler;
    });
  }
  // CommonJS / plain JS: try require, fall back to dynamic import.
  try {
    const mod = require(file);
    let handler = mod.handler || mod.default || (mod.__esModule && mod.default);
    if (typeof handler !== "function") {
      handler = undefined;
    }
    if (handler) return Promise.resolve(handler);
  } catch (err) {
    if (err.code === "ERR_REQUIRE_ESM") {
      return import(pathToFileURL(file).href).then((mod) => {
        const handler = mod.handler || mod.default;
        if (typeof handler !== "function") {
          throw new Error(`Function ${path.basename(file)} has no callable 'handler'.`);
        }
        return handler;
      });
    }
    throw err;
  }
  throw new Error(`Function ${path.basename(file)} has no callable 'handler'.`);
}

function functionFile(name) {
  if (!FUNCTION_DIR) return null;
  for (const ext of FUNCTION_EXTS) {
    const p = path.join(FUNCTION_DIR, name + ext);
    const fs = require("fs");
    try {
      if (fs.statSync(p).isFile()) return p;
    } catch (e) { /* not found */ }
  }
  return null;
}

/** Translate an HTTP request into a Netlify / Lambda event object. */
function buildEvent(method, rawPath, query, headers, bodyBuf) {
  const qs = require("querystring");
  const multi = qs.parse(query);
  const single = {};
  for (const k of Object.keys(multi)) {
    single[k] = Array.isArray(multi[k]) ? multi[k][multi[k].length - 1] : multi[k];
  }
  let isBase64Encoded = false;
  let body = "";
  if (bodyBuf && bodyBuf.length > 0) {
    body = bodyBuf.toString("base64");
    isBase64Encoded = true;
  }
  return {
    path: rawPath,
    httpMethod: method,
    headers: headers,
    queryStringParameters: single,
    multiValueQueryStringParameters: multi,
    body,
    isBase64Encoded,
    rawUrl: query ? `${rawPath}?${query}` : rawPath,
    rawQuery: query,
  };
}

/** Minimal stand-in for the Netlify context object. */
const context = {
  callbackWaitsForEmptyEventLoop: true,
  getRemainingTimeInMillis: () => 10000,
};

/** Build a Request-like object for Netlify Edge Function handlers. */
function buildRequestLike(method, rawPath, query, headers, bodyBuf) {
  const host = headers.host || "127.0.0.1";
  const fullUrl = `http://${host}${rawPath}${query ? "?" + query : ""}`;
  const bodyStr = bodyBuf ? bodyBuf.toString() : undefined;
  return {
    url: fullUrl,
    method,
    headers,
    body: bodyStr,
    json: async () => JSON.parse(bodyStr || "{}"),
    text: async () => bodyStr || "",
  };
}

const server = http.createServer((req, res) => {
  handle(req, res).catch((err) => {
    console.error("[functions] unhandled error:", err);
    sendJson(res, 500, { error: String((err && err.message) || err) });
  });
});

async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const rawPath = url.pathname;
  const query = url.search.replace(/^\?/, "");
  const method = req.method;

  let bodyBuf = Buffer.alloc(0);
  if (method === "POST" || method === "PUT" || method === "PATCH") {
    bodyBuf = await readBody(req);
  }

  if (rawPath === FUNCTIONS_PREFIX || rawPath === FUNCTIONS_PREFIX + "/") {
    return sendJson(res, 200, listFunctions());
  }

  if (rawPath.startsWith(FUNCTIONS_PREFIX + "/")) {
    const name = rawPath.slice(FUNCTIONS_PREFIX.length + 1).split("/")[0];
    const file = functionFile(name);
    if (!file) return sendJson(res, 404, { error: "Function not found" });
    try {
      const handler = await loadHandler(file);
      const event = buildEvent(method, rawPath, query, req.headers, bodyBuf);
      const requestLike = buildRequestLike(method, rawPath, query, req.headers, bodyBuf);

      let result;
      try {
        result = await handler(event, context);
      } catch (lambdaErr) {
        try {
          const response = await handler(requestLike);
          if (response && typeof response.status === "number" && typeof response.text === "function") {
            const body = await response.text();
            result = {
              statusCode: response.status,
              headers: Object.fromEntries(response.headers.entries()),
              body,
            };
          } else {
            result = response;
          }
        } catch (edgeErr) {
          throw lambdaErr;
        }
      }

      return sendFunctionResult(res, result);
    } catch (err) {
      console.error(`[functions] ${name} threw:`, err);
      return sendJson(res, 500, { error: String((err && err.message) || err) });
    }
  }

  // Everything else: reverse-proxy to the real site.
  return proxy(req, res, method, rawPath, query, bodyBuf);
}

function listFunctions() {
  if (!FUNCTION_DIR) return [];
  const fs = require("fs");
  let entries;
  try {
    entries = fs.readdirSync(FUNCTION_DIR);
  } catch (e) { return []; }
  return entries
    .filter((f) => FUNCTION_EXTS.includes(path.extname(f)))
    .map((f) => path.basename(f, path.extname(f)))
    .sort();
}

function sendFunctionResult(res, result) {
  const status = Number(result.statusCode ?? 200);
  const headers = result.headers || {};
  let raw;
  try {
    raw = result.isBase64Encoded
      ? Buffer.from(result.body || "", "base64")
      : Buffer.from(typeof result.body === "string" ? result.body : JSON.stringify(result.body ?? ""));
  } catch (e) {
    raw = Buffer.from(String(result.body ?? ""));
  }
  res.writeHead(status, headers);
  res.end(raw);
}

function proxy(req, res, method, rawPath, query, bodyBuf) {
  const target = new URL(rawPath + (query ? "?" + query : ""), SITE_URL);
  const headers = { ...req.headers };
  delete headers.host;
  delete headers["content-length"];
  const preq = http.request(
    target,
    { method, headers },
    (pres) => {
      const chunks = [];
      pres.on("data", (c) => chunks.push(c));
      pres.on("end", () => {
        const out = Buffer.concat(chunks);
        const rheaders = { ...pres.headers };
        delete rheaders["content-length"];
        delete rheaders["transfer-encoding"];
        delete rheaders.connection;
        res.writeHead(pres.statusCode, rheaders);
        res.end(out);
      });
    }
  );
  preq.on("error", (err) =>
    sendJson(res, 502, { error: "Upstream site unreachable", detail: String(err.message || err) })
  );
  preq.end(bodyBuf);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function sendJson(res, status, obj) {
  const payload = JSON.stringify(obj);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(payload);
}

function main() {
  const fs = require("fs");
  if (!FUNCTION_DIR || !fs.existsSync(FUNCTION_DIR) || !fs.statSync(FUNCTION_DIR).isDirectory()) {
    console.error(`WEBIFY_FUNCTION_DIR is not a directory: '${FUNCTION_DIR}'`);
    return 2;
  }
  if (!/^https?:\/\//.test(SITE_URL)) {
    console.error(`WEBIFY_SITE_URL must be an http(s) URL: '${SITE_URL}'`);
    return 2;
  }
  console.log(`Webify Functions gateway on http://${BIND}:${PORT}`);
  console.log(`  functions dir : ${FUNCTION_DIR}`);
  console.log(`  proxying to   : ${SITE_URL}`);
  server.listen(PORT, BIND);
  return 0;
}

if (require.main === module) {
  process.exitCode = main();
}

module.exports = { server, listFunctions, loadHandler };
