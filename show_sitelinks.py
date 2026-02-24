"""Verify sitelink metrics for both campaigns."""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import requests
from utils.aws_helpers import get_google_ads_credentials

API_VERSION = "v23"
BASE_URL = f"https://googleads.googleapis.com/{API_VERSION}"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CORE_BRAND_CAMPAIGN_ID = "22483972722"
REPLACEMENT_NETS_CAMPAIGN_ID = "22494027316"


def get_token(creds):
    resp = requests.post(TOKEN_URL, data={
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def search(creds, token, query):
    url = f"{BASE_URL}/customers/{creds['client_customer_id']}/googleAds:searchStream"
    headers = {
        "Authorization": f"Bearer {token}",
        "developer-token": creds["developer_token"],
        "login-customer-id": creds["customer_id"],
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json={"query": query.strip()})
    if not resp.ok:
        print(f"  ERROR {resp.status_code}: {resp.text[:500]}")
        return []
    results = []
    for chunk in resp.json():
        for row in chunk.get("results", []):
            results.append(row)
    return results


def show_campaign(creds, token, name, campaign_id):
    print(f"\n{'=' * 75}")
    print(f"  {name} — Sitelink Performance (lifetime)")
    print(f"{'=' * 75}")

    query = f"""
    SELECT
      campaign.id,
      campaign_asset.asset,
      campaign_asset.status,
      asset.sitelink_asset.link_text,
      asset.sitelink_asset.description1,
      asset.sitelink_asset.description2,
      asset.final_urls,
      metrics.impressions,
      metrics.clicks,
      metrics.conversions,
      metrics.conversions_value,
      metrics.cost_micros
    FROM campaign_asset
    WHERE campaign.id = {campaign_id}
      AND campaign_asset.field_type = 'SITELINK'
      AND campaign_asset.status = 'ENABLED'
    """
    rows = search(creds, token, query)

    print(f"{'Sitelink':<30} {'Impr':>8} {'Clicks':>7} {'CTR':>6} {'Cost':>9} {'Conv':>5}")
    print("-" * 75)

    for r in sorted(rows, key=lambda x: int(x.get("metrics", {}).get("clicks", 0)), reverse=True):
        sl = r.get("asset", {}).get("sitelinkAsset", {})
        m = r.get("metrics", {})
        impr = int(m.get("impressions", 0))
        clicks = int(m.get("clicks", 0))
        ctr = f"{clicks/impr*100:.1f}%" if impr > 0 else "0.0%"
        cost = int(m.get("costMicros", 0)) / 1_000_000
        conv = float(m.get("conversions", 0))
        print(f"{sl.get('linkText', '?'):<30} {impr:>8,} {clicks:>7,} {ctr:>6} ${cost:>8,.2f} {conv:>5.0f}")


def main():
    creds = get_google_ads_credentials()
    token = get_token(creds)
    show_campaign(creds, token, "Core Brand", CORE_BRAND_CAMPAIGN_ID)
    show_campaign(creds, token, "Replacement Nets", REPLACEMENT_NETS_CAMPAIGN_ID)


if __name__ == "__main__":
    main()
