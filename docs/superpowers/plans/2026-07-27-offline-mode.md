# Offline Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `general.offline_mode` server config flag that disables every KonomiTV feature requiring external internet access (jikkyo comments, Niconico/Twitter/Bluesky integrations, GitHub version check, data-broadcasting internet proxy), with "disabled + note" client UX.

**Architecture:** One bool in `config.yaml` → server-side enforcement via a shared FastAPI 503 dependency (router-level for wholly-external routers, per-endpoint for shared routers) plus a choke-point guard on background NX-Jikkyo polling → the flag reaches the client automatically through `GET /api/settings/server`, where an `is_offline_mode` store getter drives disabled-with-note UI.

**Tech Stack:** Python 3.11 / FastAPI / Pydantic v2 (server), Vue 3 + Pinia + Vuetify (client, mix of Options API and Composition API per file).

**Spec:** `docs/superpowers/specs/2026-07-27-offline-mode-design.md`

**Branch:** `offline-mode` (already created; spec is committed on it)

## Global Constraints

- Work in `/home/a1678991/IdeaProjects/KonomiTV` on branch `offline-mode`.
- **This repo has no test suite.** Verification = linters + typecheckers: after ANY Python edit run `cd server && poetry run task lint`; after ANY TypeScript/Vue edit run `cd client && yarn lint && yarn typecheck`. Both must pass with 0 errors before each commit. (Ruff auto-fixes; re-check after.)
- Never run `python` directly — always `poetry run python` from `server/`. Never start the KonomiTV server (`task serve`/`task dev`) — a user-managed instance is already running.
- Python: single quotes, UpperCamelCase functions, comments in Japanese, log messages in English, `from app import logging` (never `import logging`), Annotated Pydantic style, trailing commas in multi-line collections.
- Vue/TS: single quotes, keep Options API components in Options API style, UI strings in Japanese.
- Comment density: generous Japanese comments explaining intent (see neighboring code).
- Commit messages: Japanese one-line summary (repo convention), ending with the trailer line `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` separated by a blank line.
- Preserve all existing comments in edited code unless the edit makes them wrong.

---

### Task 1: Server config field `general.offline_mode`

**Files:**
- Modify: `server/app/config.py` (class `_ServerSettingsGeneral`, ~line 141)
- Modify: `config.example.yaml` (general section, after `program_update_interval`, ~line 43)

**Interfaces:**
- Produces: `Config().general.offline_mode: bool` (default `False`) — used by every later server task.

- [ ] **Step 1: Add the field to `_ServerSettingsGeneral`**

In `server/app/config.py`, the class currently reads:

```python
class _ServerSettingsGeneral(BaseModel):
    backend: Literal['EDCB', 'Mirakurun'] = 'EDCB'
    always_receive_tv_from_mirakurun: bool = False
    edcb_url: Annotated[Url, UrlConstraints(allowed_schemes=['tcp'])] = Url('tcp://127.0.0.1:4510/')
    mirakurun_url: Annotated[Url, UrlConstraints(allowed_schemes=['http', 'https'])] = Url('http://127.0.0.1:40772/')
    encoder: Literal['FFmpeg', 'QSVEncC', 'NVEncC', 'VCEEncC', 'rkmppenc'] = 'FFmpeg'
    program_update_interval: Annotated[float, confloat(ge=0.1)] = 5.0
    debug: bool = False
    debug_encoder: bool = False
```

Insert `offline_mode: bool = False` between `program_update_interval` and `debug`:

```python
    program_update_interval: Annotated[float, confloat(ge=0.1)] = 5.0
    offline_mode: bool = False
    debug: bool = False
```

No validator is needed (plain bool).

- [ ] **Step 2: Add the key to `config.example.yaml`**

In `config.example.yaml`, after the `program_update_interval: 5.0` block and before the `# デバッグモードを有効にするか` comment, insert (4-space indent, matching the file):

```yaml
    # オフラインモードを有効にするか
    # 有効にすると、外部インターネット接続を必要とする機能がすべて無効になります。
    # 具体的には、ニコニコ実況コメント (実況勢いの取得を含む)・ニコニコアカウント連携・Twitter / Bluesky 連携・
    # アップデートチェック・データ放送のインターネット接続機能が無効になります。
    # インターネットに接続できない環境や、外部にアクセスさせたくない環境向けの設定です。
    # なお、KonomiTV の HTTPS 化に利用している Akebi HTTPS Server はインターネット接続を必要とするため、
    # 完全にインターネットから隔離された環境では、別途 server セクションの custom_https_certificate /
    # custom_https_private_key の設定が必要です。
    offline_mode: false

```

- [ ] **Step 3: Verify the model accepts the field**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/server && poetry run python -c "from app.config import _ServerSettingsGeneral; print(_ServerSettingsGeneral.model_fields['offline_mode'].default)"
```

Expected output: `False`

- [ ] **Step 4: Run server lint**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/server && poetry run task lint
```

Expected: Ruff no errors, Pyright `0 errors, 0 warnings`.

- [ ] **Step 5: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add server/app/config.py config.example.yaml && git commit -m "$(cat <<'EOF'
サーバー設定に offline_mode を追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `VerifyNotOfflineMode` dependency + external-router guards

**Files:**
- Modify: `server/app/utils/__init__.py` (imports at top, function appended at end of file)
- Modify: `server/app/routers/TwitterRouter.py` (router definition, ~line 32)
- Modify: `server/app/routers/BlueskyRouter.py` (router definition, ~line 26)
- Modify: `server/app/routers/NiconicoRouter.py` (router definition, ~line 20)

**Interfaces:**
- Consumes: `Config().general.offline_mode` (Task 1).
- Produces: `async def VerifyNotOfflineMode() -> None` in `app.utils` — raises `HTTPException(503)` when offline; used again in Task 3.

- [ ] **Step 1: Add the dependency function to `server/app/utils/__init__.py`**

Add to the import block at the top (it currently has no fastapi import):

```python
from fastapi import HTTPException, status
```

Append at the end of the file:

```python
async def VerifyNotOfflineMode() -> None:
    """
    オフラインモードが有効なときに 503 エラーを送出する FastAPI の依存性関数
    外部インターネット接続を必要とする API ルーターやエンドポイントの dependencies に指定して利用する

    Raises:
        HTTPException: オフラインモードが有効な場合 (503 Service Unavailable)
    """

    # 循環参照を避けるために遅延インポート
    ## app.config は app.utils.TSInformation をインポートしているため、モジュールトップレベルではインポートできない
    from app.config import Config

    # オフラインモード時は、外部インターネット接続を必要とする機能を一律で無効化する
    if Config().general.offline_mode is True:
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = 'This feature is disabled because the server is running in offline mode',
        )
```

- [ ] **Step 2: Guard TwitterRouter**

In `server/app/routers/TwitterRouter.py`: ensure `Depends` is in the `from fastapi import (...)` list (it already is — the router uses `Depends(GetCurrentUser)`), and add the import `from app.utils import VerifyNotOfflineMode` (extend the existing `from app.utils import ...` line if one exists, otherwise add a new line in the `app.` import group). Then change the router definition:

```python
# ルーター
router = APIRouter(
    tags = ['Twitter'],
    prefix = '/api/twitter',
    dependencies = [Depends(VerifyNotOfflineMode)],
)
```

- [ ] **Step 3: Guard BlueskyRouter**

Same edit in `server/app/routers/BlueskyRouter.py`: add the import `from app.utils import VerifyNotOfflineMode` (`Depends` is already imported — the router uses `Depends(GetCurrentUser)`), then:

```python
# ルーター
router = APIRouter(
    tags = ['Bluesky'],
    prefix = '/api/bluesky',
    dependencies = [Depends(VerifyNotOfflineMode)],
)
```

- [ ] **Step 4: Guard NiconicoRouter**

In `server/app/routers/NiconicoRouter.py`, line 15 currently reads `from app.utils import Interlaced` — extend it:

```python
from app.utils import Interlaced, VerifyNotOfflineMode
```

Then:

```python
# ルーター
router = APIRouter(
    tags = ['Niconico'],
    prefix = '/api/niconico',
    dependencies = [Depends(VerifyNotOfflineMode)],
)
```

(`Depends` is already imported on line 7.)

- [ ] **Step 5: Run server lint**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/server && poetry run task lint
```

Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add server/app/utils/__init__.py server/app/routers/TwitterRouter.py server/app/routers/BlueskyRouter.py server/app/routers/NiconicoRouter.py && git commit -m "$(cat <<'EOF'
オフラインモード時に Twitter / Bluesky / ニコニコ連携 API を 503 で無効化

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Guards in shared routers (Channels / Videos / Version / DataBroadcasting)

**Files:**
- Modify: `server/app/routers/ChannelsRouter.py` (jikkyo endpoint decorator, ~line 531)
- Modify: `server/app/routers/VideosRouter.py` (`VideoJikkyoCommentsAPI`, ~line 725)
- Modify: `server/app/routers/VersionRouter.py` (`VersionInformationAPI`, ~line 40)
- Modify: `server/app/routers/DataBroadcastingRouter.py` (both proxy endpoints + internet-status)

**Interfaces:**
- Consumes: `VerifyNotOfflineMode` (Task 2), `Config().general.offline_mode` (Task 1).

- [ ] **Step 1: ChannelsRouter — 503 on the jikkyo WebSocket info endpoint**

In `server/app/routers/ChannelsRouter.py`, add the import `from app.utils import VerifyNotOfflineMode` (extend an existing `from app.utils import ...` line if present). `Depends` is already imported (line 10). Change the endpoint decorator:

```python
@router.get(
    '/{channel_id}/jikkyo',
    summary = 'ニコニコ実況 WebSocket URL API',
    response_description = 'ニコニコ実況コメント送受信用 WebSocket API の情報。',
    response_model = schemas.JikkyoWebSocketInfo,
    # オフラインモード時は 503 を返す (通常クライアント側で事前にオフラインモードを判定するため、これは保険的なガード)
    dependencies = [Depends(VerifyNotOfflineMode)],
)
```

- [ ] **Step 2: VideosRouter — error-shaped response for past-log comments**

In `server/app/routers/VideosRouter.py`, add `from app.config import Config` to the imports (the file does not import it yet; place it right after the `from app import logging, schemas` line, matching other routers). Then insert at the very top of the `VideoJikkyoCommentsAPI` function body (before the `# チャンネル情報と録画開始時刻/録画終了時刻の情報がある場合のみ` comment):

```python
    # オフラインモード時はニコニコ実況 過去ログ API へのアクセスを行わず、エラーメッセージを返す
    ## エラーメッセージはクライアントのコメントパネルにそのまま表示される
    if Config().general.offline_mode is True:
        return schemas.JikkyoComments(
            is_success = False,
            comments = [],
            detail = 'サーバーがオフラインモードのため、過去ログコメントを取得できません。',
        )
```

- [ ] **Step 3: VersionRouter — skip the GitHub API call**

In `server/app/routers/VersionRouter.py` (`Config` is already imported), the fetch condition currently reads:

```python
    # GitHub API で KonomiTV の最新のタグ (=最新バージョン) を取得
    ## GitHub API は無認証だと60回/1時間までしかリクエストできないので、リクエスト結果を10分ほどキャッシュする
    if latest_version is None or (time.time() - latest_version_updated_at) > 60 * 10:
```

Change to:

```python
    # GitHub API で KonomiTV の最新のタグ (=最新バージョン) を取得
    ## GitHub API は無認証だと60回/1時間までしかリクエストできないので、リクエスト結果を10分ほどキャッシュする
    ## オフラインモード時は外部アクセスを行わず、最新バージョンは常に None (未取得) のままとする
    ## latest_version が None の場合、クライアントは「アップデートなし」として扱う
    if Config().general.offline_mode is False and (latest_version is None or (time.time() - latest_version_updated_at) > 60 * 10):
```

- [ ] **Step 4: DataBroadcastingRouter — guard proxy + internet-status**

In `server/app/routers/DataBroadcastingRouter.py`:

Change line 7 to add `Depends`:

```python
from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request, status
```

Add imports after `from app.constants import API_REQUEST_HEADERS`:

```python
from app.config import Config
from app.utils import VerifyNotOfflineMode
```

Add `dependencies = [Depends(VerifyNotOfflineMode)]` to **both** proxy endpoint decorators (GET ~line 25, POST ~line 94):

```python
@router.get(
    '/request/{request_url:path}',
    summary = 'データ放送ブラウザ HTTP (GET) リクエストプロキシ API',
    response_description = 'リクエスト URL に対する GET リクエストのレスポンス。',
    # オフラインモード時は 503 を返し、任意 URL へのプロキシアクセスをサーバー側でも禁止する
    dependencies = [Depends(VerifyNotOfflineMode)],
)
```

```python
@router.post(
    '/request/{request_url:path}',
    summary = 'データ放送ブラウザ HTTP (POST) リクエストプロキシ API',
    response_description = 'リクエスト URL に対する POST リクエストのレスポンス。',
    # オフラインモード時は 503 を返し、任意 URL へのプロキシアクセスをサーバー側でも禁止する
    dependencies = [Depends(VerifyNotOfflineMode)],
)
```

Insert at the very top of the `BMLBrowserInternetStatusAPI` function body (before the `# ICMP を使用する場合は...` comment):

```python
    # オフラインモード時は外部への疎通確認を行わず、常に接続不可として返す
    if Config().general.offline_mode is True:
        return schemas.DataBroadcastingInternetStatus(
            success = False,
            ip_address = None,
            response_time_milliseconds = None,
        )
```

- [ ] **Step 5: Run server lint**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/server && poetry run task lint
```

Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add server/app/routers/ChannelsRouter.py server/app/routers/VideosRouter.py server/app/routers/VersionRouter.py server/app/routers/DataBroadcastingRouter.py && git commit -m "$(cat <<'EOF'
オフラインモード時に実況コメント・バージョンチェック・データ放送プロキシ API を無効化

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Stop background NX-Jikkyo polling + startup log

**Files:**
- Modify: `server/app/models/Channel.py` (`updateJikkyoStatus`, ~line 630)
- Modify: `server/app/app.py` (`Startup()`, ~line 223)

**Interfaces:**
- Consumes: `Config().general.offline_mode` (Task 1). `Channel.py` already imports `Config` (line 17); `app.py` already has a module-level `CONFIG`.

- [ ] **Step 1: Choke-point guard in `Channel.updateJikkyoStatus()`**

In `server/app/models/Channel.py`, the method currently starts:

```python
    @classmethod
    async def updateJikkyoStatus(cls) -> None:
        """ チャンネル情報のうち、ニコニコ実況関連のステータスを更新する """

        # 全ての実況チャンネルのステータスを更新
        await JikkyoClient.updateStatuses()
```

Insert the guard right after the docstring:

```python
    @classmethod
    async def updateJikkyoStatus(cls) -> None:
        """ チャンネル情報のうち、ニコニコ実況関連のステータスを更新する """

        # オフラインモード時は NX-Jikkyo へのアクセスを行わない
        ## このメソッドは起動時・番組情報更新時・30秒間隔の定期実行タスクすべてから呼ばれる唯一の窓口のため、
        ## ここで抑止するだけでニコニコ実況ステータス更新に伴う外部アクセスを完全に止められる
        ## jikkyo_force は None のままとなり、クライアントは実況勢いをデータなしとして表示する
        if Config().general.offline_mode is True:
            return

        # 全ての実況チャンネルのステータスを更新
        await JikkyoClient.updateStatuses()
```

- [ ] **Step 2: One-time startup log in `app.py`**

In `server/app/app.py`, `Startup()` currently begins:

```python
@app.on_event('startup')
async def Startup():
    global recorded_scan_task

    # チャンネル情報を更新
    await Channel.update()
```

Insert the log right after `global recorded_scan_task`:

```python
@app.on_event('startup')
async def Startup():
    global recorded_scan_task

    # オフラインモード有効時は、外部インターネット接続を必要とする機能が無効になっている旨を起動時に一度だけログに出す
    if CONFIG.general.offline_mode is True:
        logging.info('Offline mode is enabled. Features that require external internet access are disabled.')

    # チャンネル情報を更新
    await Channel.update()
```

- [ ] **Step 3: Run server lint**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/server && poetry run task lint
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add server/app/models/Channel.py server/app/app.py && git commit -m "$(cat <<'EOF'
オフラインモード時に NX-Jikkyo への定期ステータス更新を停止

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Client type mirror + `is_offline_mode` store getter

**Files:**
- Modify: `client/src/services/Settings.ts` (`IServerSettings` ~line 111, `IServerSettingsDefault` ~line 142)
- Modify: `client/src/stores/ServerSettingsStore.ts`

**Interfaces:**
- Consumes: server now returns `general.offline_mode` in `GET /api/settings/server` (Task 1).
- Produces: `serverSettingsStore.is_offline_mode: boolean` (computed; `false` until settings are loaded) and `serverSettingsStore.fetchServerSettingsOnce()` (existing) — used by Tasks 7–10.

- [ ] **Step 1: Mirror the field in `IServerSettings` and `IServerSettingsDefault`**

In `client/src/services/Settings.ts`, keep the same field order as the Python side (between `program_update_interval` and `debug`) — in **both** places:

```typescript
        program_update_interval: number;
        offline_mode: boolean;
        debug: boolean;
```

```typescript
        program_update_interval: 5.0,
        offline_mode: false,
        debug: false,
```

- [ ] **Step 2: Add the `is_offline_mode` computed to `ServerSettingsStore`**

In `client/src/stores/ServerSettingsStore.ts` (Composition API store): change the vue import to `import { computed, ref } from 'vue';`, then add after the `fetch_promise` declaration:

```typescript
    // サーバーがオフラインモードで動作しているかどうか
    // サーバー設定が未取得の間はデフォルト値を信用せず、常に false を返す (誤ってオフラインモード扱いにしないため)
    // 参照する側は事前に fetchServerSettingsOnce() を呼び、取得完了後に判定すること
    const is_offline_mode = computed(() => {
        if (is_loaded.value !== true) {
            return false;
        }
        return server_settings.value.general.offline_mode;
    });
```

And add `is_offline_mode,` to the returned object (after `is_loading,`).

- [ ] **Step 3: Run client lint + typecheck**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/client && yarn lint && yarn typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add client/src/services/Settings.ts client/src/stores/ServerSettingsStore.ts && git commit -m "$(cat <<'EOF'
クライアントにサーバー設定 offline_mode の型定義と is_offline_mode ゲッターを追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Offline mode switch in the server settings UI

**Files:**
- Modify: `client/src/views/Settings/Server.vue` (~line 96, before the `debug` switch)

**Interfaces:**
- Consumes: `IServerSettings.general.offline_mode` (Task 5). The page already round-trips the full settings object via `Settings.fetchServerSettings()` / `Settings.updateServerSettings()`.

- [ ] **Step 1: Add the v-switch**

In `client/src/views/Settings/Server.vue`, insert between the `program_update_interval` slider item (`</div>` after the `v-slider`) and the `debug` switch item:

```html
            <div class="settings__item settings__item--switch">
                <label class="settings__item-heading" for="offline_mode">オフラインモードを有効にする</label>
                <label class="settings__item-label" for="offline_mode">
                    有効にすると、外部インターネット接続を必要とする機能 (ニコニコ実況コメント・ニコニコアカウント連携・Twitter / Bluesky 連携・アップデートチェック・データ放送のインターネット接続機能) がすべて無効になります。<br>
                    インターネットに接続できない環境や、外部にアクセスさせたくない環境向けの設定です。<br>
                    なお、HTTPS 化に利用している Akebi HTTPS Server はインターネット接続を必要とするため、完全にインターネットから隔離された環境では、別途カスタム HTTPS 証明書/秘密鍵の設定が必要です。<br>
                </label>
                <v-switch class="settings__item-switch" color="primary" id="offline_mode" hide-details
                    v-model="server_settings.general.offline_mode">
                </v-switch>
            </div>
```

- [ ] **Step 2: Run client lint + typecheck**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/client && yarn lint && yarn typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add client/src/views/Settings/Server.vue && git commit -m "$(cat <<'EOF'
サーバー設定画面にオフラインモードのスイッチを追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Gate live & past-log comments in the player

**Files:**
- Modify: `client/src/services/player/managers/LiveCommentManager.ts` (imports; `init()` ~line 102; `initWatchSession()` ~line 120)
- Modify: `client/src/services/player/PlayerController.ts` (imports ~line 21; danmaku `read` callback ~line 494)

**Interfaces:**
- Consumes: `useServerSettingsStore().is_offline_mode` + `fetchServerSettingsOnce()` (Task 5).
- Produces: comment panel shows the offline note via existing `player_store.live_comment_init_failed_message` / `video_comment_init_failed_message` mechanisms; no error toasts.

- [ ] **Step 1: LiveCommentManager — early return in `initWatchSession()`**

Add the import (between `usePlayerStore` and `useSettingsStore` imports):

```typescript
import useServerSettingsStore from '@/stores/ServerSettingsStore';
```

`initWatchSession()` currently begins:

```typescript
    private async initWatchSession(): Promise<IWatchSessionInfo> {
        const channels_store = useChannelsStore();
        const settings_store = useSettingsStore();
        const user_store = useUserStore();

        // サーバーから disconnect メッセージが送られてきた際のフラグ
        let is_disconnect_message_received = false;
```

Change to:

```typescript
    private async initWatchSession(): Promise<IWatchSessionInfo> {
        const channels_store = useChannelsStore();
        const server_settings_store = useServerSettingsStore();
        const settings_store = useSettingsStore();
        const user_store = useUserStore();

        // サーバーがオフラインモードの場合、実況コメント機能自体を無効化する
        // このエラーメッセージはコメントパネルにそのまま表示される (init() 側で予期されたエラーとして扱われ、プレイヤー通知は行われない)
        await server_settings_store.fetchServerSettingsOnce();
        if (server_settings_store.is_offline_mode === true) {
            return {
                is_success: false,
                detail: 'サーバーがオフラインモードのため、実況コメントを利用できません。',
            };
        }

        // サーバーから disconnect メッセージが送られてきた際のフラグ
        let is_disconnect_message_received = false;
```

- [ ] **Step 2: LiveCommentManager — treat the offline message as expected in `init()`**

`init()` currently contains:

```typescript
            // 通常発生しないエラーメッセージ (サーバーエラーなど) はプレイヤー側にも通知する
            if (watch_session_info.detail !== 'このチャンネルはニコニコ実況に対応していません。') {
                if (this.player.template.notice.textContent!.includes('再起動しています…') === false) {
                    this.player.notice(watch_session_info.detail, undefined, undefined, '#FF6F6A');
                }
            }
```

Change to:

```typescript
            // 通常発生しないエラーメッセージ (サーバーエラーなど) はプレイヤー側にも通知する
            // 予期されたエラーメッセージ (実況チャンネル非対応・オフラインモード) はコメントパネルにのみ表示し、プレイヤー通知は行わない
            const expected_error_messages = [
                'このチャンネルはニコニコ実況に対応していません。',
                'サーバーがオフラインモードのため、実況コメントを利用できません。',
            ];
            if (expected_error_messages.includes(watch_session_info.detail) === false) {
                if (this.player.template.notice.textContent!.includes('再起動しています…') === false) {
                    this.player.notice(watch_session_info.detail, undefined, undefined, '#FF6F6A');
                }
            }
```

- [ ] **Step 3: PlayerController — gate the video past-log comment fetch**

Add the import (between `usePlayerStore` and `useSettingsStore` imports, ~line 21):

```typescript
import useServerSettingsStore from '@/stores/ServerSettingsStore';
```

In the danmaku `apiBackend.read` callback, the video branch currently begins:

```typescript
                    } else {
                        // ビデオ視聴: 過去ログコメントを取得して返す
                        const jikkyo_comments = await Videos.fetchVideoJikkyoComments(player_store.recorded_program.id);
                        if (jikkyo_comments.is_success === false) {
```

Change the beginning of the branch so the offline check wraps the fetch (the trailing `danmaku.seek()` sync code stays outside and still runs):

```typescript
                    } else {
                        // サーバーがオフラインモードの場合は過去ログコメントを取得せず、コメントリストにその旨を表示する
                        const server_settings_store = useServerSettingsStore();
                        await server_settings_store.fetchServerSettingsOnce();
                        if (server_settings_store.is_offline_mode === true) {
                            // コメント0件として扱う (エラートーストは表示しない)
                            player_store.video_comment_init_failed_message = 'サーバーがオフラインモードのため、過去ログコメントを利用できません。';
                            options.success([]);
                        } else {
                            // ビデオ視聴: 過去ログコメントを取得して返す
                            const jikkyo_comments = await Videos.fetchVideoJikkyoComments(player_store.recorded_program.id);
                            if (jikkyo_comments.is_success === false) {
```

Then re-indent the rest of the existing fetch/success handling (up to and including `options.success(jikkyo_comments.comments);` and its closing brace) one level deeper, and close the new `} else {` block before the `// コメント表示をシーク状態に同期する` comment. The final structure of the branch must be:

```typescript
                    } else {
                        // サーバーがオフラインモードの場合は過去ログコメントを取得せず、コメントリストにその旨を表示する
                        const server_settings_store = useServerSettingsStore();
                        await server_settings_store.fetchServerSettingsOnce();
                        if (server_settings_store.is_offline_mode === true) {
                            // コメント0件として扱う (エラートーストは表示しない)
                            player_store.video_comment_init_failed_message = 'サーバーがオフラインモードのため、過去ログコメントを利用できません。';
                            options.success([]);
                        } else {
                            // ビデオ視聴: 過去ログコメントを取得して返す
                            const jikkyo_comments = await Videos.fetchVideoJikkyoComments(player_store.recorded_program.id);
                            if (jikkyo_comments.is_success === false) {
                                // (existing failure handling, indented one level deeper, unchanged content)
                            } else {
                                // (existing success handling, indented one level deeper, unchanged content)
                            }
                        }
                        // コメント表示をシーク状態に同期する
                        // (existing seek/scroll sync code, unchanged)
```

(Keep every existing comment and statement; only indentation and the new wrapper change.)

- [ ] **Step 4: Run client lint + typecheck**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/client && yarn lint && yarn typecheck
```

Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add client/src/services/player/managers/LiveCommentManager.ts client/src/services/player/PlayerController.ts && git commit -m "$(cat <<'EOF'
オフラインモード時は実況コメント・過去ログコメントを取得せずパネルに案内を表示

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Offline note in the Twitter watch panel

**Files:**
- Modify: `client/src/components/Watch/Panel/Twitter.vue` (template ~lines 2–190; script imports ~line 193+; `computed` ~line 334; `created()` ~line 505; styles after line 1271)

**Interfaces:**
- Consumes: `serverSettingsStore.is_offline_mode` via `mapStores` (Task 5).

- [ ] **Step 1: Add store to the Options API component**

Add the import (alphabetically with the other store imports):

```typescript
import useServerSettingsStore from '@/stores/ServerSettingsStore';
```

Extend the `mapStores` call (line ~334):

```typescript
        ...mapStores(useChannelsStore, usePlayerStore, useServerSettingsStore, useSettingsStore, useUserStore, useTwitterStore),
```

At the top of `async created()` (line ~505), before `await this.userStore.fetchUser();`, add:

```typescript
        // オフラインモード判定のためにサーバー設定を取得しておく
        await this.serverSettingsStore.fetchServerSettingsOnce();
```

- [ ] **Step 2: Add the offline note and wrap the existing content**

In the template, right after the opening `<div class="twitter-container">` (line 2), insert:

```html
        <div class="twitter-offline-announce" v-if="serverSettingsStore.is_offline_mode">
            <div class="twitter-offline-announce__heading">Twitter / Bluesky 連携機能は利用できません。</div>
            <div class="twitter-offline-announce__text">
                <p class="mt-0 mb-0">サーバーがオフラインモードのため、Twitter / Bluesky 連携機能を利用できません。</p>
            </div>
        </div>
        <template v-else>
```

and insert the closing `</template>` immediately before the final `</div>` that closes `.twitter-container` (currently line ~189, just before `</template>` of the SFC template block). All existing children (`.tab-container`, `.tab-button-container`, `.tweet-form`, the account list) end up inside the `<template v-else>`. Re-indent the wrapped content one level (or leave indentation as-is if the diff would be huge — project precedent favors correct indentation; re-indent).

- [ ] **Step 3: Add the announce styles**

In the `<style lang="scss" scoped>` block (starts line ~1271), add inside `.twitter-container`'s scope (or top-level in the style block if simpler — match file structure):

```scss
.twitter-offline-announce {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    height: 100%;
    padding-left: 12px;
    padding-right: 12px;

    &__heading {
        font-size: 20px;
        font-weight: bold;
        text-align: center;
    }

    &__text {
        margin-top: 12px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 13.5px;
        text-align: center;
        line-height: 1.65;
    }
}
```

- [ ] **Step 4: Run client lint + typecheck**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/client && yarn lint && yarn typecheck
```

Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add client/src/components/Watch/Panel/Twitter.vue && git commit -m "$(cat <<'EOF'
オフラインモード時は視聴画面の Twitter パネルに案内を表示

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Disabled-with-note treatment on settings pages

**Files:**
- Modify: `client/src/views/Settings/Jikkyo.vue` (template lines 11–; `computed` ~line 213; `created()` ~line 215)
- Modify: `client/src/views/Settings/Twitter.vue` (template line 11; `computed` ~line 450; `created()` ~line 478)
- Modify: `client/src/views/Settings/DataBroadcasting.vue` (internet-access switch item ~line 27; script imports ~line 80; `computed` ~line 174; `created()` ~line 210)

**Interfaces:**
- Consumes: `serverSettingsStore.is_offline_mode` via `mapStores` (Task 5). Global CSS `settings__content--disabled` (opacity + pointer-events none) and `settings__item--disabled` (opacity) exist in `client/src/views/Settings/Base.vue`; `settings__quote` also exists there.

- [ ] **Step 1: Jikkyo.vue**

Script: add the import `import useServerSettingsStore from '@/stores/ServerSettingsStore';` (alphabetically among store imports), extend `mapStores`:

```typescript
        ...mapStores(useServerSettingsStore, useSettingsStore, useUserStore),
```

At the top of `async created()`, add:

```typescript
        // オフラインモード判定のためにサーバー設定を取得しておく
        await this.serverSettingsStore.fetchServerSettingsOnce();
```

Template: after the closing `</h2>` and before `<div class="settings__content" ...>`, insert:

```html
        <div class="settings__quote mt-5" v-if="serverSettingsStore.is_offline_mode">
            サーバーがオフラインモードのため、ニコニコ実況連携機能は利用できません。<br>
        </div>
```

Change the content div's class binding (line 11) to also disable everything when offline:

```html
        <div class="settings__content" :class="{'settings__content--loading': is_loading, 'settings__content--disabled': serverSettingsStore.is_offline_mode}">
```

- [ ] **Step 2: Settings/Twitter.vue**

Same three edits: store import + `mapStores` extension:

```typescript
        ...mapStores(useServerSettingsStore, useSettingsStore, useUserStore),
```

`created()` prefix:

```typescript
        // オフラインモード判定のためにサーバー設定を取得しておく
        await this.serverSettingsStore.fetchServerSettingsOnce();
```

Template banner after `</h2>`:

```html
        <div class="settings__quote mt-5" v-if="serverSettingsStore.is_offline_mode">
            サーバーがオフラインモードのため、Twitter / Bluesky 連携機能は利用できません。<br>
        </div>
```

Content div class binding (line 11):

```html
        <div class="settings__content" :class="{'settings__content--loading': is_loading, 'settings__content--disabled': serverSettingsStore.is_offline_mode}">
```

- [ ] **Step 3: DataBroadcasting.vue — disable only the internet-access switch**

Script: add the import `import useServerSettingsStore from '@/stores/ServerSettingsStore';`, extend `mapStores`:

```typescript
        ...mapStores(useServerSettingsStore, useSettingsStore),
```

At the top of `created()`, add (this `created()` is not async — the fetch is fire-and-forget; the computed updates reactively when it resolves):

```typescript
        // オフラインモード判定のためにサーバー設定を取得しておく (取得完了後に is_offline_mode がリアクティブに反映される)
        this.serverSettingsStore.fetchServerSettingsOnce();
```

Template: change the `enable_internet_access_from_data_broadcasting` item's opening div to:

```html
            <div class="settings__item settings__item--switch settings__item--sync-disabled"
                :class="{'settings__item--disabled': serverSettingsStore.is_offline_mode}">
```

Add a note label just before its `<v-switch>`:

```html
                <label class="settings__item-label text-error-lighten-1" v-if="serverSettingsStore.is_offline_mode" for="enable_internet_access_from_data_broadcasting">
                    サーバーがオフラインモードのため、この設定を有効にしてもデータ放送からインターネットにアクセスすることはできません。<br>
                </label>
```

And disable the switch itself:

```html
                <v-switch class="settings__item-switch" color="primary" id="enable_internet_access_from_data_broadcasting" hide-details
                    :disabled="serverSettingsStore.is_offline_mode"
                    v-model="settingsStore.settings.enable_internet_access_from_data_broadcasting">
                </v-switch>
```

- [ ] **Step 4: Run client lint + typecheck**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/client && yarn lint && yarn typecheck
```

Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add client/src/views/Settings/Jikkyo.vue client/src/views/Settings/Twitter.vue client/src/views/Settings/DataBroadcasting.vue && git commit -m "$(cat <<'EOF'
オフラインモード時は各設定ページに案内を表示して操作を無効化

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Force data-broadcasting internet access off at runtime

**Files:**
- Modify: `client/src/services/player/managers/LiveDataBroadcastingManager.ts` (imports ~line 11; `init()` ~line 89; guards at ~lines 237, 246, 270, 305)

**Interfaces:**
- Consumes: `useServerSettingsStore().is_offline_mode` + `fetchServerSettingsOnce()` (Task 5). Server-side 503 (Task 3) is the backstop if settings aren't loaded yet.

- [ ] **Step 1: Add the import**

Between the `useChannelsStore` and `useSettingsStore` imports:

```typescript
import useServerSettingsStore from '@/stores/ServerSettingsStore';
```

- [ ] **Step 2: Prefetch server settings in `init()`**

`init()` currently begins:

```typescript
    public async init(): Promise<void> {
        const channels_store = useChannelsStore();

        const is_data_broadcasting_enabled = useSettingsStore().settings.tv_show_data_broadcasting;
```

Change to:

```typescript
    public async init(): Promise<void> {
        const channels_store = useChannelsStore();

        // オフラインモード判定のためにサーバー設定を取得しておく
        // 以降の各ガードは is_offline_mode を同期的に参照するため、ここで事前に取得を済ませておく必要がある
        await useServerSettingsStore().fetchServerSettingsOnce();

        const is_data_broadcasting_enabled = useSettingsStore().settings.tv_show_data_broadcasting;
```

- [ ] **Step 3: Extend the four internet-access guards**

There are four guard sites (the `isIPConnected()` positive check plus three negative early-return checks in `get()`, `transmitTextDataOverIP()`, `confirmIPNetwork()`).

Change `isIPConnected()`'s body check from:

```typescript
                        if (useSettingsStore().settings.enable_internet_access_from_data_broadcasting === true) {
                            return 1;
                        } else {
                            return 0;
                        }
```

to:

```typescript
                        // サーバーがオフラインモードのときは、設定に関わらず IP 接続なしとして扱う
                        if (useSettingsStore().settings.enable_internet_access_from_data_broadcasting === true &&
                            useServerSettingsStore().is_offline_mode === false) {
                            return 1;
                        } else {
                            return 0;
                        }
```

For each of the three early-return guards, change the pattern:

```typescript
                        // データ放送からのインターネットアクセスが無効なときは何もしない
                        if (useSettingsStore().settings.enable_internet_access_from_data_broadcasting === false) {
```

to:

```typescript
                        // データ放送からのインターネットアクセスが無効なとき、またはサーバーがオフラインモードのときは何もしない
                        if (useSettingsStore().settings.enable_internet_access_from_data_broadcasting === false ||
                            useServerSettingsStore().is_offline_mode === true) {
```

(keeping each guard's existing return value: `{}` in `get()`, the `resultCode: NaN` object in `transmitTextDataOverIP()`, `null` in `confirmIPNetwork()`).

- [ ] **Step 4: Run client lint + typecheck**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/client && yarn lint && yarn typecheck
```

Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git add client/src/services/player/managers/LiveDataBroadcastingManager.ts && git commit -m "$(cat <<'EOF'
オフラインモード時はデータ放送からのインターネットアクセスを強制的に無効化

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full lint pass on both sides**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV/server && poetry run task lint
cd /home/a1678991/IdeaProjects/KonomiTV/client && yarn lint && yarn typecheck
```

Expected: 0 errors everywhere.

- [ ] **Step 2: Confirm clean working tree and commit log**

```bash
cd /home/a1678991/IdeaProjects/KonomiTV && git status --short && git log --oneline master..offline-mode
```

Expected: no uncommitted changes; ~11 commits (spec + 10 implementation commits).

- [ ] **Step 3: Report the manual smoke-test checklist to the user**

The dev server is user-managed, so hand the user this checklist instead of restarting anything:

1. Set `offline_mode: true` in `config.yaml`, restart the KonomiTV server.
2. `server/logs/KonomiTV-Server.log` shows `Offline mode is enabled. ...` once at startup; no NX-Jikkyo requests afterwards.
3. `curl -s https://my.local.konomi.tv:7000/api/version` → `latest_version: null`.
4. `curl -s -o /dev/null -w '%{http_code}' https://my.local.konomi.tv:7000/api/twitter/...` (any Twitter/Bluesky/Niconico endpoint, and `/api/channels/gr011/jikkyo`, `/api/data-broadcasting/request/http://example.com`) → `503`.
5. Watch screen: comment panel shows 「サーバーがオフラインモードのため、実況コメントを利用できません。」, Twitter panel shows the offline note, no red toasts.
6. Video playback: comment panel shows the past-log offline note.
7. Settings → ニコニコ実況 / Twitter: banner shown, controls greyed out. データ放送: internet switch disabled. サーバー設定: offline mode switch visible and saveable.
8. Set `offline_mode: false`, restart → everything behaves as before.
