from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent

from app.config import Settings
from app.data_access import ShoppingDataStore, build_data_tools
from app.prompts import (
    DATA_WORKER_PROMPT,
    POLICY_WORKER_PROMPT,
    RESPONSE_WORKER_PROMPT,
    SUPERVISOR_PROMPT,
)
from app.state import ShoppingState
from app.utils import (
    extract_json_payload,
    get_last_ai_content,
    list_worker_tools,
    serialize_message,
    timestamp_utc,
)
from provider import get_chat_model
from rag.embeddings import SentenceTransformerEmbeddings
from rag.vector_store import ChromaPolicyStore


class ShoppingAssistant:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        self._llm = get_chat_model(self.settings)
        self._data_store = ShoppingDataStore(self.settings.orders_path)
        self._embedding_model = SentenceTransformerEmbeddings(
            self.settings.embedding_model_name
        )
        self._policy_store = ChromaPolicyStore(
            persist_directory=self.settings.chroma_dir,
            embedding_model=self._embedding_model,
        )
        self._policy_store.ensure_index(self.settings.policy_path)
        self._data_tools = build_data_tools(self._data_store)
        self.graph = build_graph(
            self._llm, self._policy_store, self.settings, self._data_tools
        )

    def ask(
        self,
        question: str,
        trace_file: Path | None = None,
        rebuild_index: bool = False,
    ) -> dict[str, Any]:
        if rebuild_index:
            self._policy_store.rebuild(self.settings.policy_path)

        initial_state: ShoppingState = {"question": question, "trace": []}
        result = self.graph.invoke(initial_state)

        payload = {
            "question": question,
            "route": result.get("route", {}),
            "policy_result": result.get("policy_result", {}),
            "data_result": result.get("data_result", {}),
            "final_answer": result.get("final_answer", ""),
            "trace": result.get("trace", []),
        }

        if trace_file is not None:
            trace_file = Path(trace_file)
            trace_file.parent.mkdir(parents=True, exist_ok=True)
            trace_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        return payload

    def run_batch(
        self,
        test_file: Path,
        output_dir: Path,
        rebuild_index: bool = False,
    ) -> dict[str, Any]:
        test_cases = json.loads(Path(test_file).read_text(encoding="utf-8"))
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for case in test_cases:
            qid = case.get("id", "unknown")
            question = case["question"]
            trace_file = output_dir / f"trace_{qid}.json"

            result = None
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    result = self.ask(question, trace_file=trace_file, rebuild_index=False)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(4 * (attempt + 1))

            if result is not None:
                passed = _evaluate_case(case, result)
                results.append({
                    "id": qid,
                    "question": question,
                    "status": "ok",
                    "passed": passed,
                    "final_answer": result.get("final_answer", ""),
                    "route": result.get("route", {}),
                })
            else:
                results.append({
                    "id": qid,
                    "question": question,
                    "status": "error",
                    "passed": False,
                    "error": str(last_error),
                })

        total = len(results)
        passed_count = sum(1 for r in results if r.get("passed"))
        summary = {
            "generated_at": timestamp_utc(),
            "total": total,
            "passed": passed_count,
            "failed": total - passed_count,
            "results": results,
        }
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary


def _retry(fn: Any, retries: int = 3, base_delay: float = 3.0) -> Any:
    """Retry fn up to `retries` times with linear backoff on any exception."""
    last_exc: Exception | None = None
    for i in range(retries):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if i < retries - 1:
                time.sleep(base_delay * (i + 1))
    raise last_exc  # type: ignore[misc]


def _evaluate_case(case: dict, result: dict) -> bool:
    expected_status = case.get("expected_status", "")
    route = result.get("route", {})
    final_answer = result.get("final_answer", "")

    if expected_status == "clarification_needed":
        return route.get("status") == "clarification_needed"
    if expected_status == "not_found":
        policy = result.get("policy_result", {})
        data = result.get("data_result", {})
        return (
            data.get("status") == "not_found"
            or policy.get("status") == "not_found"
            or "not_found" in final_answer
        )

    expected_contains = case.get("expected_contains", [])
    for keyword in expected_contains:
        if keyword.lower() not in final_answer.lower():
            return False
    return True


def build_graph(
    llm: Any,
    policy_store: ChromaPolicyStore,
    settings: Settings,
    data_tools: list,
) -> Any:
    top_k = settings.top_k

    @tool
    def search_policy(query: str) -> str:
        """Search the VinShop policy knowledge base for information about
        shipping, returns, refunds, vouchers, and platform rules."""
        hits = policy_store.search(query, top_k=top_k)
        return json.dumps(hits, ensure_ascii=False)

    policy_agent = create_react_agent(llm, [search_policy])
    data_agent = create_react_agent(llm, data_tools)

    def _supervisor_node(state: ShoppingState) -> dict:
        question = state["question"]
        messages = [
            SystemMessage(content=SUPERVISOR_PROMPT),
            HumanMessage(content=f"Câu hỏi của người dùng: {question}"),
        ]
        response = _retry(lambda: llm.invoke(messages))
        route = extract_json_payload(str(response.content))
        if not route:
            route = {
                "status": "ok",
                "needs_policy": True,
                "needs_data": False,
                "clarification_question": None,
            }
        trace_entry = {
            "node": "supervisor",
            "timestamp": timestamp_utc(),
            "route": route,
            "messages": [serialize_message(m) for m in messages + [response]],
        }
        return {"route": route, "trace": [trace_entry]}

    def _worker_1_policy_node(state: ShoppingState) -> dict:
        question = state["question"]
        result = _retry(lambda: policy_agent.invoke({
            "messages": [
                HumanMessage(content=f"{POLICY_WORKER_PROMPT}\n\nCâu hỏi: {question}")
            ]
        }))
        last_content = get_last_ai_content(result["messages"])
        policy_result = extract_json_payload(last_content) or {
            "status": "ok",
            "summary": last_content,
            "facts": [],
            "citations": [],
        }
        trace_entry = {
            "node": "worker_1_policy",
            "timestamp": timestamp_utc(),
            "tools_used": list_worker_tools(result["messages"]),
            "messages": [serialize_message(m) for m in result["messages"]],
        }
        return {"policy_result": policy_result, "trace": [trace_entry]}

    def _worker_2_data_node(state: ShoppingState) -> dict:
        question = state["question"]
        result = _retry(lambda: data_agent.invoke({
            "messages": [
                HumanMessage(content=f"{DATA_WORKER_PROMPT}\n\nCâu hỏi: {question}")
            ]
        }))
        last_content = get_last_ai_content(result["messages"])
        data_result = extract_json_payload(last_content) or {
            "status": "ok",
            "summary": last_content,
            "facts": [],
            "missing_fields": [],
            "not_found_entities": [],
        }
        trace_entry = {
            "node": "worker_2_data",
            "timestamp": timestamp_utc(),
            "tools_used": list_worker_tools(result["messages"]),
            "messages": [serialize_message(m) for m in result["messages"]],
        }
        return {"data_result": data_result, "trace": [trace_entry]}

    def _worker_3_response_node(state: ShoppingState) -> dict:
        question = state["question"]
        route = state.get("route", {})
        policy_result = state.get("policy_result", {})
        data_result = state.get("data_result", {})

        context_parts = [
            f"Câu hỏi: {question}",
            f"Routing: {json.dumps(route, ensure_ascii=False)}",
        ]
        if policy_result:
            context_parts.append(
                f"Kết quả Policy Worker:\n{json.dumps(policy_result, ensure_ascii=False)}"
            )
        if data_result:
            context_parts.append(
                f"Kết quả Data Worker:\n{json.dumps(data_result, ensure_ascii=False)}"
            )

        messages = [
            SystemMessage(content=RESPONSE_WORKER_PROMPT),
            HumanMessage(content="\n\n".join(context_parts)),
        ]
        response = _retry(lambda: llm.invoke(messages))
        final_answer = str(response.content)

        trace_entry = {
            "node": "worker_3_response",
            "timestamp": timestamp_utc(),
            "messages": [serialize_message(m) for m in messages + [response]],
        }
        return {"final_answer": final_answer, "trace": [trace_entry]}

    def _route_after_supervisor(state: ShoppingState) -> str:
        route = state.get("route", {})
        if route.get("status") == "clarification_needed":
            return "respond"
        needs_policy = route.get("needs_policy", False)
        needs_data = route.get("needs_data", False)
        if needs_policy:
            return "policy"
        if needs_data:
            return "data"
        return "respond"

    def _route_after_policy(state: ShoppingState) -> str:
        route = state.get("route", {})
        if route.get("needs_data", False):
            return "data"
        return "respond"

    graph = StateGraph(ShoppingState)
    graph.add_node("supervisor", _supervisor_node)
    graph.add_node("worker_1", _worker_1_policy_node)
    graph.add_node("worker_2", _worker_2_data_node)
    graph.add_node("worker_3", _worker_3_response_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {"policy": "worker_1", "data": "worker_2", "respond": "worker_3"},
    )
    graph.add_conditional_edges(
        "worker_1",
        _route_after_policy,
        {"data": "worker_2", "respond": "worker_3"},
    )
    graph.add_edge("worker_2", "worker_3")
    graph.add_edge("worker_3", END)

    return graph.compile()
