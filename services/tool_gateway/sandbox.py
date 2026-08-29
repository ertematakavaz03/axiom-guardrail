from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from services.tool_gateway.registry import (
    CreateTicketInput,
    GetOrderInput,
    RefundOrderInput,
    SearchCustomerInput,
    ToolInput,
)

CUSTOMERS = {
    "CUS-1001": {"customer_id": "CUS-1001", "email": "ada@example.com", "name": "Ada Lovelace"},
    "CUS-1002": {"customer_id": "CUS-1002", "email": "grace@example.com", "name": "Grace Hopper"},
}
ORDERS = {
    "ORD-1001": {
        "order_id": "ORD-1001",
        "customer_id": "CUS-1001",
        "total": 49.99,
        "status": "delivered",
    },
    "ORD-1002": {
        "order_id": "ORD-1002",
        "customer_id": "CUS-1002",
        "total": 129.0,
        "status": "shipped",
    },
}


class SupportSandbox:
    def __init__(self) -> None:
        self.customers = deepcopy(CUSTOMERS)
        self.orders = deepcopy(ORDERS)
        self.refunds: list[dict[str, Any]] = []
        self.tickets: list[dict[str, Any]] = []

    async def execute(self, name: str, arguments: ToolInput) -> dict[str, Any]:
        if name == "search_customer" and isinstance(arguments, SearchCustomerInput):
            for customer in self.customers.values():
                if (
                    customer["email"] == arguments.email
                    or customer["customer_id"] == arguments.customer_id
                ):
                    return {"found": True, "customer": customer}
            return {"found": False, "customer": None}
        if name == "get_order" and isinstance(arguments, GetOrderInput):
            order = self.orders.get(arguments.order_id.upper())
            return {"found": order is not None, "order": order}
        if name == "create_ticket" and isinstance(arguments, CreateTicketInput):
            ticket = {"ticket_id": f"TKT-{uuid.uuid4().hex[:8].upper()}", **arguments.model_dump()}
            self.tickets.append(ticket)
            return {"created": True, "ticket": ticket}
        if name == "refund_order" and isinstance(arguments, RefundOrderInput):
            order = self.orders.get(arguments.order_id.upper())
            if order is None:
                return {"refunded": False, "reason": "ORDER_NOT_FOUND"}
            refund = {"refund_id": f"REF-{uuid.uuid4().hex[:8].upper()}", **arguments.model_dump()}
            self.refunds.append(refund)
            return {"refunded": True, "refund": refund}
        raise ValueError(f"Unsupported sandbox tool: {name}")
