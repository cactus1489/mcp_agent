import asyncio
import json
import time
import sys
from pathlib import Path

from pydantic_ai.messages import RetryPromptPart, ToolCallPart

from agent.src.app import create_agent


async def main():
    agent = create_agent([])

    @agent.tool_plain
    def pesticide_lookup(crop_name: str, disease_weed_name: str) -> dict:
        """작물과 병해충에 등록된 농약을 조회한다. crop_name은 필수 작물명이며 추측하지 않는다."""
        raise AssertionError("이 테스트의 질문에는 조회에 필요한 작물명이 없습니다.")

    cases = [
        ("하이", "NOT_APPLICABLE"),
        ("넌 누구야?", "NOT_APPLICABLE"),
        ("흰가루병에 등록된 농약을 조회해 줘.", "NEED_MORE_INFO"),
        ("하이, 흰가루병에 등록된 농약을 조회해 줘.", "NEED_MORE_INFO"),
    ]
    if "--input-only" in sys.argv:
        cases = cases[2:]
    rows = []
    for question, expected in cases:
        started = time.perf_counter()
        row = {"question": question, "expected": expected}
        try:
            async with asyncio.timeout(45):
                result = await agent.run(question)
            parts = [p for m in result.all_messages() for p in m.parts]
            row.update(
                status=result.output.status,
                answer=result.output.answer,
                retries=sum(isinstance(p, RetryPromptPart) for p in parts),
                retry_details=[p.content for p in parts if isinstance(p, RetryPromptPart)],
                lookup_calls=sum(isinstance(p, ToolCallPart) and p.tool_name == "pesticide_lookup"
                                 for p in parts),
                model_requests=result.usage.requests,
            )
            row["passed"] = (row["status"] == expected and row["retries"] == 0
                             and row["lookup_calls"] == 0 and row["model_requests"] == 1)
            if expected == "NOT_APPLICABLE":
                row["passed"] &= row["answer"] == "요청을 처리할 수 있는 MCP 도구가 없습니다."
        except Exception as exc:
            row.update(error_type=type(exc).__name__, passed=False)
        row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    Path(__file__).with_name("results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


asyncio.run(main())
