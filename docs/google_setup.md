# Google Cloud Setup Guide

This guide walks through the steps to configure Google Cloud for Office Admin, including the Calendar and Gmail APIs.

---

## 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project dropdown at the top of the page, then click **New Project**.
3. Enter a project name (e.g., `Office Admin`) and click **Create**.
4. Once created, select the new project from the dropdown to make it active.

---

## 2. Configure the OAuth Consent Screen

The app must be configured as a test application before credentials can be issued.

1. In the left sidebar, go to **APIs & Services > OAuth consent screen**.
2. Select **External** as the user type and click **Create**.
3. Fill in the required fields:
   - **App name**: e.g., `Office Admin`
   - **User support email**: your email address
   - **Developer contact information**: your email address
4. Click **Save and Continue** through the Scopes and Optional Info steps (scopes are added in a later step).
5. On the **Test users** step, click **Add Users** and add the Google account(s) that will use the app during testing.
6. Click **Save and Continue**, then **Back to Dashboard**.

> While the app is in **Testing** status, only the accounts listed as test users can authorize it. Publishing is not required for personal or internal use.

---

## 3. Enable the Google Calendar API

1. In the left sidebar, go to **APIs & Services > Library**.
2. Search for **Google Calendar API** and select it.
3. Click **Enable**.

---

## 4. Enable the Gmail API

1. In the left sidebar, go to **APIs & Services > Library**.
2. Search for **Gmail API** and select it.
3. Click **Enable**.

---

## 5. Create OAuth 2.0 Credentials

Separate credential files are needed for Calendar and Gmail because each goes through its own authorization flow and stores its own token.

### Calendar credentials

1. Go to **APIs & Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Select **Desktop app** as the application type.
4. Name it (e.g., `Office Admin Calendar`) and click **Create**.
5. Click **Download JSON** and save the file as `calendar_credentials.json` in the project root.

### Gmail credentials

1. Repeat the steps above.
2. Name it (e.g., `Office Admin Gmail`) and click **Create**.
3. Click **Download JSON** and save the file as `gmail_credentials.json` in the project root.

---

## 6. Configure OAuth Scopes

Scopes define what access the app requests during authorization.

1. Go to **APIs & Services > OAuth consent screen** and click **Edit App**.
2. Proceed to the **Scopes** step and click **Add or Remove Scopes**.
3. Add the following scopes:

| Scope | Purpose |
|---|---|
| `https://www.googleapis.com/auth/calendar.readonly` | Read calendar events |
| `https://www.googleapis.com/auth/gmail.compose` | Create and send email drafts |

4. Click **Update**, then **Save and Continue** through the remaining steps.

---

## 7. First-Run Authorization

On first launch, the app will open a browser window for each service (Calendar and Gmail) to complete the OAuth flow.

- Sign in with a test user account.
- Review the requested permissions and click **Allow**.
- Tokens are saved automatically (`calendar_token.json` and `gmail_token.json`) so subsequent runs do not require re-authorization.

> If you see a warning that the app is unverified, click **Advanced > Go to \<App Name\> (unsafe)**. This is expected for apps in Testing status.
