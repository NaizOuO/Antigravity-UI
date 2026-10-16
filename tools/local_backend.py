"""本地模型後端：用 Antigravity SDK 接 Ollama／LM Studio 這類 OpenAI 相容的伺服器。

輸出跟 agy 的 --output-format stream-json 同樣格式（init、step_update、result），
所以 server.py 只要換掉啟動的指令，後面的解析、畫面都不用改。
權限判斷、完成檢查走 Lumina 原本的 hook 網址（跟 agy 的 hooks.json 用同一套判斷）。
SDK 是選用元件（components/agsdk），由 server.py 用 PYTHONPATH 帶進來。

用法：python local_backend.py --prompt-file F --model qwen3:8b --base-url http://localhost:11434/v1
      --cwd 工作區 [--work 工作資料夾] [--conversation ID] --save-dir 對話存放處 --hook-url URL --stop-url URL
"""
import argparse
import asyncio
import json
import sys
import urllib.request
import uuid
from pathlib import Path

from google.antigravity import Agent, LocalOpenAIAgentConfig, types
from google.antigravity.hooks import policy, pre_tool_call_decide, stop

ap = argparse.ArgumentParser()
for a in ("--prompt-file", "--model", "--base-url", "--cwd", "--save-dir", "--hook-url", "--stop-url"):
    ap.add_argument(a, required=True)
ap.add_argument("--work")
ap.add_argument("--add-dir", action="append", default=[])
ap.add_argument("--conversation")
ARGS = ap.parse_args()
CID = ARGS.conversation or uuid.uuid4().hex
CWD = Path(ARGS.cwd)


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def post(url, payload, timeout):
    req = urllib.request.Request(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


AGY_NAMES = {"create_file": "write_to_file", "edit_file": "replace_file_content"}   # SDK 的名稱 → agy 的名稱（Lumina 的判斷用 agy 的）


def tool_name(n):
    n = str(getattr(n, "value", n))
    return AGY_NAMES.get(n, n)


@pre_tool_call_decide
async def guard(call: types.ToolCall):
    """每個工具呼叫都先問 Lumina（可能會跳授權卡等使用者按，所以等久一點）。"""
    payload = {"conversationId": CID, "toolCall": {"name": str(tool_name(call.name)), "args": call.args or {}}}
    try:
        r = await asyncio.to_thread(post, ARGS.hook_url, payload, 3600)
    except Exception as e:
        return types.HookResult(allow=False, message=f"Lumina 沒有回應權限判斷（{e}），這個操作先不執行。")
    if r.get("decision") == "deny":
        return types.HookResult(allow=False, message=r.get("reason") or "使用者或安全設定不允許這個操作。")
    return types.HookResult(allow=True, modified_args=r.get("overwrite") or None)


@stop
async def finish_check(data: types.StopArgs):
    try:
        r = await asyncio.to_thread(post, ARGS.stop_url, {"conversationId": CID}, 20)
    except Exception:
        return types.StopHookResult()
    if r.get("decision") == "continue" and r.get("reason"):
        return types.StopHookResult(decision=types.StopDecision.CONTINUE, reason=r["reason"])
    return types.StopHookResult()


def mcp_servers():
    try:
        cfg = json.loads((CWD / ".agents" / "mcp_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [types.McpStdioServer(name=k, command=v["command"], args=v.get("args") or [], env=v.get("env"))
            for k, v in (cfg.get("mcpServers") or {}).items() if isinstance(v, dict) and v.get("command")]


def rules_text():
    """agy 會自己讀 .agents/rules；SDK 不會，所以把一直生效的規則、和其他規則的說明放進系統指示。"""
    parts = []
    for f in sorted((CWD / ".agents" / "rules").glob("*.md")):
        text = f.read_text(encoding="utf-8")
        head, _, body = text.partition("---")[2].partition("---")
        if "always_on" in head:
            parts.append(body.strip())
        elif "description:" in head:
            desc = head.split("description:", 1)[1].strip().splitlines()[0]
            parts.append(f"（需要時用 view_file 讀 {f}：{desc}）")
    return "\n\n".join(parts)


async def main():
    prompt = Path(ARGS.prompt_file).read_text(encoding="utf-8")
    spaces = [str(CWD)] + ([ARGS.work] if ARGS.work else []) + ARGS.add_dir
    Path(ARGS.save_dir).mkdir(parents=True, exist_ok=True)
    cfg = LocalOpenAIAgentConfig(
        model=ARGS.model, base_url=ARGS.base_url, workspaces=spaces,
        policies=[policy.allow_all()],            # 真正的判斷在上面的 guard（跟 agy 用同一套）
        hooks=[guard, finish_check],
        system_instructions="一律用繁體中文回答。\n\n" + rules_text(),
        skills_paths=[str(p) for p in [CWD / ".agents" / "skills"] if p.is_dir()],
        mcp_servers=mcp_servers(),
        conversation_id=CID, save_dir=ARGS.save_dir,
    ).lightweight()
    cfg.session_continuation_mode = types.SessionContinuationMode.CREATE_OR_RESUME
    out({"event": "init", "conversation_id": CID, "model": ARGS.model})
    reply = ""
    async with Agent(cfg) as agent:
        resp = await agent.chat(prompt)
        async for ch in resp.chunks:
            if isinstance(ch, types.Text):
                reply += ch.text
                out({"step_update": {"step_type": "agent_response", "text_delta": ch.text}})
            elif isinstance(ch, types.Thought):
                out({"event": "thinking", "text": ch.text})
            elif isinstance(ch, types.ToolCall):
                out({"step_update": {"step_type": "tool", "state": "ACTIVE", "tool_name": str(tool_name(ch.name)),
                                     "tool_info": {"parameters": ch.args or {}}}})
        try:
            total = agent.conversation.total_usage.total_token_count
        except Exception:
            total = None
    out({"event": "result", "result": {"response": reply, "conversation_id": CID,
                                       "usage": {"total_tokens": total} if total else {}}})


try:
    asyncio.run(main())
except Exception as e:                      # 連不到本地伺服器、模型名稱錯…：給使用者看得懂的訊息
    msg = str(e)
    if "Connect" in type(e).__name__ or "connect" in msg.lower():
        msg = f"連不到本地模型伺服器（{ARGS.base_url}）。請確認 Ollama 或 LM Studio 已經開啟。詳細：{msg}"
    out({"event": "result", "result": {"response": "", "conversation_id": CID, "error": msg[:800]}})
    print(msg[:800], file=sys.stderr)
    sys.exit(1)
