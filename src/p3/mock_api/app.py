"""Mock store API shaped like the WooCommerce REST API v3 (/wp-json/wc/v3)."""

import math
import secrets
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from p3 import SYNTHETIC_NOTICE
from p3.mock_api.faults import corrupt_order, pick_fault_ids
from p3.mock_api.store import MockStore, gmt_from_param

API_PREFIX = "/wp-json/wc/v3"
MAX_PER_PAGE = 100


class WooError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def create_app(store: MockStore, api_key: str, api_secret: str, inject_faults: int = 0) -> FastAPI:
    if not api_key or not api_secret:
        raise ValueError("STORE_API_KEY and STORE_API_SECRET must be set")
    app = FastAPI(title="Mock WooCommerce store (synthetic data)", description=SYNTHETIC_NOTICE)
    app.state.store = store
    app.state.faults = pick_fault_ids(list(store.records["orders"]), inject_faults)
    basic = HTTPBasic(auto_error=False)

    @app.exception_handler(WooError)
    async def woo_error(_: Request, exc: WooError) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message, "data": {"status": exc.status}}, exc.status)

    def require_auth(creds: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> None:
        ok = creds is not None and secrets.compare_digest(creds.username.encode(), api_key.encode()) \
            and secrets.compare_digest(creds.password.encode(), api_secret.encode())
        if not ok:
            raise WooError(401, "woocommerce_rest_cannot_view", "Sorry, you cannot list resources.")

    router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_auth)])

    def render(entity: str, record: dict) -> dict:
        kind = app.state.faults.get(record["id"]) if entity == "orders" else None
        return corrupt_order(record, kind) if kind else record

    def paginate(entity: str, request: Request, response: Response, page: int, per_page: int,
                 modified_after: str | None, dates_are_gmt: bool, orderby: str, order: str,
                 parent_id: int | None = None) -> list[dict]:
        if not 1 <= per_page <= MAX_PER_PAGE:
            raise WooError(400, "rest_invalid_param", f"per_page must be between 1 and {MAX_PER_PAGE}.")
        try:
            after = gmt_from_param(modified_after, dates_are_gmt) if modified_after else None
        except ValueError:
            raise WooError(400, "rest_invalid_param", "Invalid parameter(s): modified_after") from None
        rows = store.query(entity, after, orderby, order, parent_id)
        total, total_pages = len(rows), max(math.ceil(len(rows) / per_page), 1)
        if page > total_pages and total > 0:
            raise WooError(400, "rest_post_invalid_page_number", "The page number requested is larger than the number of pages available.")
        response.headers["X-WP-Total"] = str(total)
        response.headers["X-WP-TotalPages"] = str(total_pages)
        links = []
        if page > 1:
            links.append(f'<{request.url.include_query_params(page=page - 1)}>; rel="prev"')
        if page < total_pages:
            links.append(f'<{request.url.include_query_params(page=page + 1)}>; rel="next"')
        if links:
            response.headers["Link"] = ", ".join(links)
        start = (page - 1) * per_page
        return [render(entity, r) for r in rows[start : start + per_page]]

    def list_endpoint(entity: str):  # noqa: ANN202 - FastAPI handler factory
        def handler(request: Request, response: Response,
                    page: Annotated[int, Query(ge=1)] = 1,
                    per_page: int = 10,
                    modified_after: str | None = None,
                    dates_are_gmt: bool = False,
                    orderby: Literal["date", "id", "modified"] = "date",
                    order: Literal["asc", "desc"] = "desc") -> list[dict]:
            return paginate(entity, request, response, page, per_page, modified_after, dates_are_gmt, orderby, order)
        return handler

    for entity in ("products", "orders", "refunds"):
        router.add_api_route(f"/{entity}", list_endpoint(entity), methods=["GET"], name=f"list_{entity}")

    @router.get("/orders/{order_id}")
    def get_order(order_id: int) -> dict:
        record = store.get("orders", order_id)
        if record is None:
            raise WooError(404, "woocommerce_rest_shop_order_invalid_id", "Invalid ID.")
        return render("orders", record)

    @router.put("/orders/{order_id}")
    def update_order(order_id: int, changes: Annotated[dict, Body()]) -> dict:
        if store.get("orders", order_id) is None:
            raise WooError(404, "woocommerce_rest_shop_order_invalid_id", "Invalid ID.")
        allowed = {"status", "customer_note", "meta_data", "line_items", "billing", "shipping"}
        unknown = set(changes) - allowed
        if unknown:
            raise WooError(400, "rest_invalid_param", f"Unsupported field(s): {', '.join(sorted(unknown))}")
        return render("orders", store.update("orders", order_id, changes))

    @router.get("/orders/{order_id}/refunds")
    def order_refunds(order_id: int, request: Request, response: Response,
                      page: Annotated[int, Query(ge=1)] = 1, per_page: int = 10) -> list[dict]:
        return paginate("refunds", request, response, page, per_page, None, False, "date", "desc", order_id)

    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True, "synthetic": True, "now": datetime.now().isoformat(timespec="seconds")}

    return app
