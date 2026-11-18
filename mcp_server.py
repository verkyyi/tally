"""Tally for agents: a Model Context Protocol server over plain HTTP, at /mcp.

It is read-only and keeps nothing between calls: every POST carries one JSON-RPC message (or a list of them) and gets its
answer as JSON. The tools themselves are given by the app (main.py), which knows the record; this file only speaks the protocol.
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # protocol versions this server answers in, newest first
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type, accept, authorization, mcp-protocol-version, mcp-session-id", "Access-Control-Max-Age": "86400"}
ABOUT = ("Tally is a public record of numbers about companies and open-source projects (valuations, funding, revenue, plan and API prices, "
         "headcount, GitHub stars, downloads). Every number has its dated history and the page each value was read from. Search first, then "
         "fetch a number or its history by id. Data is CC BY 4.0: when you use a value, cite the number's page address.")


def router(tools, counted=lambda name: None):
    """tools: {name: {"title", "description", "schema", "run": fn(arguments) -> dict}}. counted(name) is called once per tool call."""
    r = APIRouter()

    def err(mid, code, text):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    def answer(msg):
        """The reply to one JSON-RPC message, or None when it asks for none (a notification)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return None if isinstance(msg, dict) and "method" not in msg else err(msg.get("id") if isinstance(msg, dict) else None, -32600, "not a JSON-RPC 2.0 request")
        if "id" not in msg:
            return None
        mid, method, p = msg["id"], msg["method"], msg.get("params") or {}
        ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
        if method == "initialize":
            asked = p.get("protocolVersion")
            return ok({"protocolVersion": asked if asked in VERSIONS else VERSIONS[0], "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "tally", "title": "Tally", "version": "1.0.0"}, "instructions": ABOUT})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": [{"name": n, "title": t["title"], "description": t["description"], "inputSchema": t["schema"],
                                  "annotations": {"title": t["title"], "readOnlyHint": True, "openWorldHint": False}} for n, t in tools.items()]})
        if method == "tools/call":
            t = tools.get(p.get("name"))
            if not t:
                return err(mid, -32602, f"unknown tool: {p.get('name')}")
            args = p.get("arguments") or {}
            if not isinstance(args, dict):
                return err(mid, -32602, "arguments must be an object")
            try:
                counted(p["name"])
                out = t["run"](args)
            except (KeyError, ValueError, TypeError) as e:  # a missing or wrong argument, or nothing found: the caller can correct it
                return ok({"content": [{"type": "text", "text": str(e).strip("'\"") or "bad arguments"}], "isError": True})
            return ok({"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "structuredContent": out, "isError": False})
        return err(mid, -32601, f"method not found: {method}")

    @r.post("/mcp")
    async def post(request: Request):
        try:
            body = json.loads(await request.body())
        except ValueError:
            return JSONResponse(err(None, -32700, "the body is not JSON"), status_code=400, headers=CORS)
        if isinstance(body, list):
            out = [a for a in (answer(m) for m in body) if a is not None]
            return JSONResponse(out, headers=CORS) if out else Response(status_code=202, headers=CORS)
        out = answer(body)
        return JSONResponse(out, headers=CORS) if out is not None else Response(status_code=202, headers=CORS)

    @r.options("/mcp")
    def options():
        return Response(status_code=204, headers=CORS)

    @r.get("/mcp")
    @r.delete("/mcp")
    def no_stream():  # this server never pushes messages and keeps no session, so there is nothing to listen to or to end
        return JSONResponse(err(None, -32000, "POST JSON-RPC messages to this address; it does not stream"), status_code=405, headers=CORS | {"Allow": "POST, OPTIONS"})

    return r
