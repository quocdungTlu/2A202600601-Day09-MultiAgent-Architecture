from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


class ShoppingDataStore:
    def __init__(self, json_path: Path) -> None:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        self.metadata = raw.get("metadata", {})
        self._customers: dict[str, Any] = {
            c["customer_id"]: c for c in raw.get("customers", [])
        }
        self._orders: dict[str, Any] = {
            str(o["order_id"]): o for o in raw.get("orders", [])
        }
        self._vouchers: list[dict] = raw.get("vouchers", [])
        self._orders_by_customer: dict[str, list] = {}
        for order in raw.get("orders", []):
            cid = order.get("customer_id", "")
            self._orders_by_customer.setdefault(cid, []).append(order)

    def get_customer_by_id(self, customer_id: str) -> dict[str, Any]:
        customer = self._customers.get(customer_id)
        if customer is None:
            return {"status": "not_found", "customer_id": customer_id}
        return {"status": "ok", "customer": customer}

    def get_orders_by_customer_id(self, customer_id: str, limit: int = 10) -> dict[str, Any]:
        if customer_id not in self._customers:
            return {"status": "not_found", "customer_id": customer_id}
        orders = self._orders_by_customer.get(customer_id, [])
        orders_sorted = sorted(orders, key=lambda o: o.get("created_at", ""), reverse=True)
        return {"status": "ok", "customer_id": customer_id, "orders": orders_sorted[:limit]}

    def get_order_detail_by_order_id(self, order_id: str) -> dict[str, Any]:
        order = self._orders.get(str(order_id))
        if order is None:
            return {"status": "not_found", "order_id": order_id}
        return {"status": "ok", "order": order}

    def get_vouchers_by_customer_id(
        self,
        customer_id: str,
        only_active: bool = False,
    ) -> dict[str, Any]:
        if customer_id not in self._customers:
            return {"status": "not_found", "customer_id": customer_id}
        vouchers = [v for v in self._vouchers if v.get("customer_id") == customer_id]
        if only_active:
            vouchers = [v for v in vouchers if v.get("status") == "active"]
        return {"status": "ok", "customer_id": customer_id, "vouchers": vouchers}


def build_data_tools(store: ShoppingDataStore) -> list:
    @tool
    def get_customer_by_id(customer_id: str) -> str:
        """Get customer profile by customer ID (e.g. 'C001').
        Returns tier, loyalty points, voucher quota, contact info."""
        return json.dumps(store.get_customer_by_id(customer_id), ensure_ascii=False)

    @tool
    def get_orders_by_customer_id(customer_id: str) -> str:
        """Get the 10 most recent orders for a customer by customer ID (e.g. 'C001').
        Returns order list with status and dates."""
        return json.dumps(store.get_orders_by_customer_id(customer_id), ensure_ascii=False)

    @tool
    def get_order_detail_by_order_id(order_id: str) -> str:
        """Get full detail of a specific order by order ID (e.g. '1971').
        Returns status, items, dates, shipping info, and payment details."""
        return json.dumps(store.get_order_detail_by_order_id(str(order_id)), ensure_ascii=False)

    @tool
    def get_vouchers_by_customer_id(customer_id: str, only_active: bool = False) -> str:
        """Get vouchers for a customer by customer ID (e.g. 'C001').
        Set only_active=True to return only currently usable vouchers."""
        return json.dumps(
            store.get_vouchers_by_customer_id(customer_id, only_active),
            ensure_ascii=False,
        )

    return [
        get_customer_by_id,
        get_orders_by_customer_id,
        get_order_detail_by_order_id,
        get_vouchers_by_customer_id,
    ]
