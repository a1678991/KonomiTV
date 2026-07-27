# Offline Mode — Design Spec (2026-07-27)

## Goal

Add an offline mode to KonomiTV, controlled by server config (`config.yaml`), that disables every feature requiring an external internet connection. Intended for deployments on isolated / internet-restricted networks. LAN-only communication (Mirakurun / EDCB backends, clients) is unaffected.

## Scope

Features disabled when `general.offline_mode: true`:

| Feature | External endpoint(s) |
|---|---|
| ニコニコ実況 live comments (WebSocket info API) | nx-jikkyo.tsukumijima.net, nicovideo.jp |
| 過去ログ comments for recordings | jikkyo.tsukumijima.net |
| NX-Jikkyo status polling (30s background task + startup/interval updates) | nx-jikkyo.tsukumijima.net |
| Niconico account linking (OAuth) | oauth.nicovideo.jp, app.konomi.tv, nvapi.nicovideo.jp |
| Twitter/X integration (incl. video proxy) | x.com, video.twimg.com, pbs.twimg.com |
| Bluesky integration | bsky.social (atproto PDS) |
| Version update check | api.github.com |
| Data broadcasting internet features (server-side proxy + internet-status) | arbitrary URLs |

**Out of scope:** Akebi HTTPS Server cannot be disabled by app config; fully isolated networks additionally require `server.custom_https_certificate` / `custom_https_private_key` (documented in the config comment). Purely browser-side external image fetches (e.g. Niconico avatar CDN) already fail harmlessly with `@error` fallbacks and are not gated.

## Decisions made during brainstorming

1. **Scope:** disable all external features (not just comments) — chosen by user.
2. **UI treatment:** show disabled UI with an explanatory note, do not hide it — chosen by user.
3. **Enforcement:** single `general.offline_mode` flag + layered guards (Approach A) — chosen by user over conditional router mounting and granular per-feature flags.

## 1. Config

- `server/app/config.py` — add `offline_mode: bool = False` to `_ServerSettingsGeneral`, placed before the `debug` fields. Plain bool; no `@field_validator` needed.
- `config.example.yaml` — add the key to the `general` section, following the existing Japanese comment style (heading line + detail lines, `。` endings). The comment must mention:
  - which features are disabled (ニコニコ実況コメント・ニコニコアカウント連携・Twitter/Bluesky 連携・アップデートチェック・データ放送のインターネット接続機能)
  - the Akebi caveat: 完全にインターネットから隔離された環境では、Akebi HTTPS Server がインターネット接続を必要とするため、別途 `server.custom_https_certificate` / `custom_https_private_key` の設定が必要
- `client/src/services/Settings.ts` — add `offline_mode: boolean;` to `IServerSettings` **and** `offline_mode: false` to `IServerSettingsDefault` (both required; keep the same field order as the Python side).
- `client/src/views/Settings/Server.vue` — add a `v-switch` for the setting, following the existing `debug` / `always_receive_tv_from_mirakurun` examples. Takes effect after server restart, same as every server setting (`SaveConfig()` never mutates the live `_CONFIG`).

## 2. Server-side enforcement

### Shared dependency

New FastAPI dependency in `server/app/utils/__init__.py`:

```python
async def VerifyNotOfflineMode() -> None:
    # オフラインモード時は 503 を返し、外部インターネット接続を必要とする機能を無効化する
    if Config().general.offline_mode is True:
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = 'This feature is disabled because the server is running in offline mode',
        )
```

Rationale for 503: unambiguous "intentionally unavailable", no collision with auth (401/403) or not-found (404) semantics.

### Router-level guards (`APIRouter(dependencies=[Depends(VerifyNotOfflineMode)])`)

- `server/app/routers/TwitterRouter.py` (covers all tweet/timeline/search endpoints and the `video-proxy` endpoint)
- `server/app/routers/BlueskyRouter.py`
- `server/app/routers/NiconicoRouter.py`

### Per-endpoint guards in shared routers

- `server/app/routers/ChannelsRouter.py` — `GET /{channel_id}/jikkyo` (`ChannelJikkyoWebSocketInfoAPI`): raise the 503 via the same dependency. Defense-in-depth; the client gates before calling.
- `server/app/routers/VideosRouter.py` — `GET /{video_id}/jikkyo`: return `JikkyoComments(is_success=False, comments=[], detail='サーバーがオフラインモードのため、過去ログコメントを取得できません。')` — the existing error shape whose `detail` the UI displays.
- `server/app/routers/VersionRouter.py` — skip the GitHub API call entirely when offline; `latest_version` stays `None` (the client already treats `None` as "no update available").
- `server/app/routers/DataBroadcastingRouter.py`:
  - `GET`/`POST /request/{request_url:path}` → 503 via the dependency. This closes the currently **unguarded** server-side proxy.
  - `GET /internet-status` → return `DataBroadcastingInternetStatus(success=False, ip_address=None, response_time_milliseconds=None)` immediately, without pinging.

### Background tasks

Single choke-point early-return at the top of `Channel.updateJikkyoStatus()` (`server/app/models/Channel.py`) when offline mode is on. This covers all three callers in `server/app/app.py`: `Startup()`, `UpdateChannelAndProgram()` (program_update_interval), and `UpdateChannelJikkyoStatus()` (every 30s). `jikkyo_force` stays `None`, which the client already renders as no-data. Log the skip once at startup (English log message), not every 30 seconds.

## 3. Client-side UX (show disabled, with note)

- `client/src/stores/ServerSettingsStore.ts` — add an `is_offline_mode` getter that returns `false` until `is_loaded === true` (existing convention from `Program.vue`'s `isEDCBBackend()` to avoid false positives from default values). Components needing the value on mount call `await serverSettingsStore.fetchServerSettingsOnce()` first.
- **Live comments** — `client/src/services/player/managers/LiveCommentManager.ts`: early return in `initWatchSession()` with `{is_success: false, detail: 'サーバーがオフラインモードのため、実況コメントを利用できません。'}`. The comment panel already renders this note via `player_store.live_comment_init_failed_message` (`Comment.vue`). Add the message to the expected-message condition in `init()` so no red `player.notice()` toast appears (same treatment as 'このチャンネルはニコニコ実況に対応していません。').
- **Past-log comments (video playback)** — `client/src/services/player/PlayerController.ts` (around the `Videos.fetchVideoJikkyoComments()` call, currently line ~501): when `is_offline_mode`, skip the fetch and set `player_store.video_comment_init_failed_message` to 「サーバーがオフラインモードのため、過去ログコメントを利用できません。」. Add that message to the existing no-toast exclusion (currently only 'この録画番組の過去ログコメントは存在しないか、現在取得中です。') so no error toast appears. The server-side `is_success=False` + `detail` response is the fallback for direct API consumers.
- **Twitter panel** — `client/src/components/Watch/Panel/Twitter.vue`: when `is_offline_mode`, replace the panel content with a centered note (「サーバーがオフラインモードのため、Twitter 連携機能を利用できません。」), styled like the comment panel's announce block.
- **Settings pages**:
  - `client/src/views/Settings/Jikkyo.vue` and `client/src/views/Settings/Twitter.vue` — note banner at top (「サーバーがオフラインモードのため、この機能は利用できません。」) and disable the link/auth buttons and switches.
  - `client/src/views/Settings/DataBroadcasting.vue` — disable the `enable_internet_access_from_data_broadcasting` switch with the same note.
- **Data broadcasting runtime** — `client/src/services/player/managers/LiveDataBroadcastingManager.ts`: treat offline mode as forcing `enable_internet_access_from_data_broadcasting` off by adding the `is_offline_mode` check to the existing guards.
- **Update notification** — no change needed: `latest_version: null` already suppresses it in `Navigation.vue` / `MyPage.vue`.

## 4. Operational notes

- All endpoints remain registered regardless of the flag, so the OpenAPI schema is config-independent.
- Changing the flag requires a server restart, identical to every other server setting.
- All new UI strings are Japanese; all new log messages and HTTP error details are English (project convention).

## 5. Verification

- `poetry run task lint` (Ruff + Pyright) in `server/`; `yarn lint; yarn typecheck` in `client/`.
- Manual smoke test with `offline_mode: true` (requires user-managed server restart):
  1. `curl` the guarded endpoints → expect 503 (Twitter/Bluesky/Niconico/jikkyo WS info/data-broadcasting proxy), `is_success: false` (video jikkyo), `latest_version: null` (version), `success: false` (internet-status).
  2. Watch screen: comment panel and Twitter panel show the offline notes; no error toasts.
  3. `server/logs/KonomiTV-Server.log`: no NX-Jikkyo requests after startup.
  4. Settings pages show banners; server settings UI can toggle the flag.
- With `offline_mode: false` (default): behavior identical to current master.
