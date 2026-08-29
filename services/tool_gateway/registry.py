from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SearchCustomerInput(BaseModel):
    email: str | None = None
    customer_id: str | None = None

    @model_validator(mode="after")
    def one_identifier(self) -> SearchCustomerInput:
        if bool(self.email) == bool(self.customer_id):
            raise ValueError("Provide exactly one of email or customer_id")
        return self


class GetOrderInput(BaseModel):
    order_id: str = Field(min_length=1)


class CreateTicketInput(BaseModel):
    customer_id: str = Field(min_length=1)
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=5000)


class RefundOrderInput(BaseModel):
    order_id: str = Field(min_length=1)
    amount: float = Field(gt=0, le=10_000)
    reason: str = Field(min_length=3, max_length=500)
    confirmed: bool


ToolInput = SearchCustomerInput | GetOrderInput | CreateTicketInput | RefundOrderInput
TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "search_customer": SearchCustomerInput,
    "get_order": GetOrderInput,
    "create_ticket": CreateTicketInput,
    "refund_order": RefundOrderInput,
}


def _tool(
    name: str,
    description: str,
    risk_class: Literal["R0", "R1", "R2"],
    side_effect: bool,
    requires_confirmation: bool,
) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "risk_class": risk_class,
        "side_effect": side_effect,
        "required_role": "member",
        "requires_confirmation": requires_confirmation,
        "input_schema": TOOL_INPUT_MODELS[name].model_json_schema(),
    }


DEMO_TOOL_REGISTRY = [
    _tool("search_customer", "Find a sandbox customer", "R0", False, False),
    _tool("get_order", "Read a sandbox order", "R0", False, False),
    _tool("create_ticket", "Create a sandbox support ticket", "R1", True, False),
    _tool("refund_order", "Refund a sandbox order", "R2", True, True),
]
