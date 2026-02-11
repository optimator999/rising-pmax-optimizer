# API Setup Guide

## Google Ads API

### 1. Get Developer Token

1. Go to your Google Ads account
2. Tools > Setup > API Center
3. Apply for a developer token
4. Wait for approval (24-48 hours)

### 2. Create OAuth2 Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create new project: "Rising PMax Optimizer"
3. Enable the Google Ads API
4. Go to APIs & Services > Credentials
5. Create OAuth2 credentials (Desktop app type)
6. Download and note the `client_id` and `client_secret`

### 3. Get Refresh Token

```bash
pip install google-ads
# Use the built-in credential generator
python -c "
from google.ads.googleads.client import GoogleAdsClient
# Follow the interactive prompts
"
```

Or use the `generate_user_credentials.py` script from the google-ads-python library.

### 4. Get Customer ID

- Top right corner of Google Ads interface
- Format: XXX-XXX-XXXX
- Remove dashes for API use: XXXXXXXXXX

### 5. Test Connection

```python
from google.ads.googleads.client import GoogleAdsClient

client = GoogleAdsClient.load_from_dict({
    'developer_token': 'YOUR_TOKEN',
    'client_id': 'YOUR_CLIENT_ID',
    'client_secret': 'YOUR_CLIENT_SECRET',
    'refresh_token': 'YOUR_REFRESH_TOKEN',
    'use_proto_plus': True
})

customer_id = 'YOUR_CUSTOMER_ID'
query = 'SELECT campaign.id, campaign.name FROM campaign'

ga_service = client.get_service('GoogleAdsService')
response = ga_service.search(customer_id=customer_id, query=query)

for row in response:
    print(f'{row.campaign.id}: {row.campaign.name}')
```

### 6. Store in Parameter Store

```bash
aws ssm put-parameter --name /Google_Ads/DEVELOPER_TOKEN --value "YOUR_TOKEN" --type SecureString
aws ssm put-parameter --name /Google_Ads/CLIENT_ID --value "YOUR_CLIENT_ID" --type SecureString
aws ssm put-parameter --name /Google_Ads/CLIENT_SECRET --value "YOUR_CLIENT_SECRET" --type SecureString
aws ssm put-parameter --name /Google_Ads/REFRESH_TOKEN --value "YOUR_REFRESH_TOKEN" --type SecureString
aws ssm put-parameter --name /Google_Ads/CUSTOMER_ID --value "1234567890" --type SecureString
```

---

## Slack App Setup

### 1. Create App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click "Create New App" > "From scratch"
3. Name: "Rising Ads Optimizer"
4. Choose your workspace

### 2. Add Bot Token Scopes

1. Navigate to OAuth & Permissions
2. Under "Bot Token Scopes", add:
   - `chat:write`
   - `files:write`
   - `users:read`

### 3. Install App

1. Click "Install to Workspace"
2. Authorize the app
3. Copy the "Bot User OAuth Token" (starts with `xoxb-`)

### 4. Get Your User ID

1. In Slack, click your profile picture
2. Click "Profile"
3. Click the three dots (...) > "Copy member ID"

### 5. Test Connection

```python
from slack_sdk import WebClient

client = WebClient(token='xoxb-YOUR-TOKEN')
response = client.chat_postMessage(
    channel='YOUR_USER_ID',
    text='Test message from Rising Ads Optimizer'
)
print(response['ok'])  # Should print True
```

### 6. Store in Parameter Store

```bash
aws ssm put-parameter --name /Slack/TOKEN --value "xoxb-YOUR-TOKEN" --type SecureString
aws ssm put-parameter --name /Slack/PMAX_CHANNEL --value "U0XXXXXXX" --type String
```

---

## Anthropic API

### 1. Get API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key
3. Copy the key (starts with `sk-ant-`)

### 2. Store in Parameter Store

```bash
aws ssm put-parameter --name /Anthropic/API_KEY --value "sk-ant-YOUR-KEY" --type SecureString
```

The system uses Claude Sonnet 4 (`claude-sonnet-4-20250514`) for generating replacement copy. Estimated cost: ~$0.03/week.
