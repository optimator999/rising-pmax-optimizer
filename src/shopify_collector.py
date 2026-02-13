"""Shopify GraphQL Admin API client for marketing attribution data.

Uses customerJourneySummary on orders to implement "last non-direct click"
attribution, matching Shopify's Marketing Attribution report methodology.

Net sales = currentSubtotalPriceSet - refunds
"""

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("rising-pmax.shopify")

SHOPIFY_API_VERSION = "2024-10"

ORDERS_QUERY = """
query OrdersWithAttribution($cursor: String, $query: String!) {
  orders(first: 100, after: $cursor, query: $query) {
    edges {
      cursor
      node {
        id
        name
        createdAt
        currentSubtotalPriceSet {
          shopMoney {
            amount
            currencyCode
          }
        }
        refunds(first: 10) {
          totalRefundedSet {
            shopMoney {
              amount
              currencyCode
            }
          }
        }
        displayFinancialStatus
        customerJourneySummary {
          ready
          firstVisit {
            source
            sourceType
            utmParameters {
              source
              medium
              campaign
            }
            referrerUrl
          }
          lastVisit {
            source
            sourceType
            utmParameters {
              source
              medium
              campaign
            }
            referrerUrl
          }
          moments(first: 20) {
            edges {
              node {
                occurredAt
                ... on CustomerVisit {
                  source
                  sourceType
                  utmParameters {
                    source
                    medium
                    campaign
                  }
                  referrerUrl
                }
              }
            }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def _to_decimal(value) -> Decimal:
    """Safely convert a value to Decimal."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _subtotal(order: dict) -> Decimal:
    """Extract currentSubtotalPriceSet from an order."""
    money = (
        order.get("currentSubtotalPriceSet", {})
        .get("shopMoney", {})
        .get("amount", "0")
    )
    return _to_decimal(money)


def _refund_total(order: dict) -> Decimal:
    """Sum all refunds for an order."""
    total = Decimal("0")

    refund_list = order.get("refunds") or []
    if not isinstance(refund_list, list):
        return total

    for ref in refund_list:
        if not ref:
            continue

        trs = ref.get("totalRefundedSet") or {}
        shop = trs.get("shopMoney") or {}
        amount_str = shop.get("amount")

        if amount_str:
            total += Decimal(amount_str)

    return total


def _net_sales(order: dict) -> float:
    """Calculate net sales: subtotal minus refunds."""
    net = _subtotal(order) - _refund_total(order)
    return float(net)


class ShopifyCollector:
    """Collects order revenue with attribution via Shopify GraphQL Admin API."""

    def __init__(self, store_url: str, access_token: str):
        self.graphql_url = (
            f"https://{store_url}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
        )
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }
        logger.info("Shopify GraphQL client initialized for %s", store_url)

    def _graphql(self, query: str, variables: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute a GraphQL query against the Shopify Admin API."""
        body: Dict[str, Any] = {"query": query}
        if variables:
            body["variables"] = variables

        resp = requests.post(
            self.graphql_url,
            headers=self.headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
        return data.get("data", {})

    def _get_orders_with_attribution(
        self, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch all orders in date range with customerJourneySummary."""
        all_orders = []
        search_query = (
            f"created_at:>={start_date} created_at:<={end_date}T23:59:59Z"
            f' NOT tag:"channel:dealer"'
        )
        cursor = None

        while True:
            variables = {"query": search_query, "cursor": cursor}
            data = self._graphql(ORDERS_QUERY, variables)
            orders_data = data.get("orders", {})

            for edge in orders_data.get("edges", []):
                all_orders.append(edge["node"])

            page_info = orders_data.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                cursor = page_info["endCursor"]
            else:
                break

        logger.info(
            "Fetched %d orders with attribution from %s to %s",
            len(all_orders), start_date, end_date,
        )
        return all_orders

    def _get_last_non_direct_visit(
        self, journey: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Find the last non-direct click from a customer journey.

        Walks moments in reverse chronological order and returns the first
        visit that isn't "Direct" or empty. Falls back to lastVisit.
        """
        if not journey:
            return None

        # If attribution isn't ready yet, use lastVisit as best guess
        if not journey.get("ready"):
            last = journey.get("lastVisit")
            if last and (last.get("source") or "").lower() not in ("", "direct"):
                return last
            return None

        # Walk moments in reverse (most recent first)
        moments = journey.get("moments", {}).get("edges", [])
        for edge in reversed(moments):
            visit = edge.get("node", {})
            source = (visit.get("source") or "").lower()
            if source and source != "direct":
                return visit

        # Fallback to lastVisit if moments didn't help
        last = journey.get("lastVisit")
        if last and (last.get("source") or "").lower() not in ("", "direct"):
            return last

        return None

    def _is_google_visit(self, visit: Optional[Dict[str, Any]]) -> bool:
        """Check if a visit is from any Google source."""
        if not visit:
            return False

        source = (visit.get("source") or "").lower()
        utm = visit.get("utmParameters") or {}
        utm_source = (utm.get("source") or "").lower()
        referrer = (visit.get("referrerUrl") or "").lower()

        return (
            "google" in source
            or "google" in utm_source
            or "www.google." in referrer
        )

    def _matches_campaign(
        self, visit: Dict[str, Any], campaign_name: str
    ) -> bool:
        """Check if a visit's UTM campaign matches the target campaign."""
        utm = visit.get("utmParameters") or {}
        utm_campaign = (utm.get("campaign") or "").lower()
        target = campaign_name.lower()

        if not utm_campaign:
            return False

        # Exact or substring match (PMax may append suffixes)
        return target in utm_campaign or utm_campaign in target

    def get_google_attributed_revenue(
        self,
        start_date: str,
        end_date: str,
        campaign_name: str = "",
    ) -> Dict[str, Any]:
        """Calculate net sales from Google-attributed orders using last non-direct click.

        Net sales = currentSubtotalPriceSet - refunds

        If campaign_name is provided, attempts campaign-level matching via UTM
        parameters. Falls back to all-Google attribution if campaign matching
        yields zero results.
        """
        all_orders = self._get_orders_with_attribution(start_date, end_date)

        # Filter out fully voided orders (keep refunded - we back out refund amounts)
        valid_orders = [
            o for o in all_orders
            if o.get("displayFinancialStatus") not in ("VOIDED",)
        ]

        google_all = []
        google_campaign = []
        attribution_debug = {"no_journey": 0, "not_google": 0, "no_campaign_match": 0}

        for order in valid_orders:
            journey = order.get("customerJourneySummary")
            last_ndc = self._get_last_non_direct_visit(journey)

            if not journey:
                attribution_debug["no_journey"] += 1
                continue

            if not self._is_google_visit(last_ndc):
                attribution_debug["not_google"] += 1
                continue

            net = _net_sales(order)
            order_info = {
                "name": order.get("name"),
                "amount": net,
                "visit": last_ndc,
            }
            google_all.append(order_info)

            if campaign_name and self._matches_campaign(last_ndc, campaign_name):
                google_campaign.append(order_info)
            elif campaign_name:
                attribution_debug["no_campaign_match"] += 1

        # Use campaign-specific if we got matches, otherwise all-Google
        if campaign_name and google_campaign:
            attributed = google_campaign
            method = "campaign_match"
        else:
            attributed = google_all
            method = "all_google"
            if campaign_name and not google_campaign:
                logger.warning(
                    "No orders matched campaign '%s' via UTM. "
                    "Using all-Google attribution (%d orders). "
                    "Debug: %s",
                    campaign_name, len(google_all), attribution_debug,
                )

        total_revenue = sum(a["amount"] for a in attributed)
        order_count = len(attributed)
        avg_order_value = total_revenue / order_count if order_count > 0 else 0.0

        total_all_revenue = sum(_net_sales(o) for o in valid_orders)
        google_share = (
            (total_revenue / total_all_revenue * 100) if total_all_revenue > 0 else 0.0
        )

        result = {
            "total_revenue": round(total_revenue, 2),
            "order_count": order_count,
            "avg_order_value": round(avg_order_value, 2),
            "total_orders_all_channels": len(valid_orders),
            "google_share_pct": round(google_share, 1),
            "attribution_method": method,
        }

        logger.info(
            "Google-attributed revenue (%s): $%.2f from %d orders "
            "(%.1f%% of total). Debug: %s",
            method, total_revenue, order_count, google_share, attribution_debug,
        )
        return result
