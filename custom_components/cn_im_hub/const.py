"""Constants for CN IM Hub."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cn_im_hub"

PROVIDER_FEISHU: Final = "feishu"
PROVIDER_WECOM: Final = "wecom"
PROVIDER_QQ: Final = "qq"
PROVIDER_DINGTALK: Final = "dingtalk"
PROVIDER_WECHAT: Final = "wechat"
PROVIDER_XIAOYI: Final = "xiaoyi"
PROVIDER_CUSTOM: Final = "custom"
PROVIDER_AGENT_MAIL: Final = "agent_mail"
PROVIDERS: Final = (
    PROVIDER_WECOM,
    PROVIDER_WECHAT,
    PROVIDER_QQ,
    PROVIDER_FEISHU,
    PROVIDER_DINGTALK,
    PROVIDER_XIAOYI,
    PROVIDER_CUSTOM,
    PROVIDER_AGENT_MAIL,
)

# 渠道默认标题（subentry 创建默认名称；不依赖 translations，避免 hassfest 自定义键校验）
PROVIDER_TITLES: Final = {
    PROVIDER_WECOM: "企业微信",
    PROVIDER_WECHAT: "微信",
    PROVIDER_QQ: "QQ",
    PROVIDER_FEISHU: "飞书",
    PROVIDER_DINGTALK: "钉钉",
    PROVIDER_XIAOYI: "小艺",
    PROVIDER_CUSTOM: "Custom",
    PROVIDER_AGENT_MAIL: "腾讯 Agent 邮箱",
}

CONF_AGENT_MAIL_ACCESS_TOKEN: Final = "access_token"
CONF_AGENT_MAIL_REFRESH_TOKEN: Final = "refresh_token"
CONF_AGENT_MAIL_ALIAS_ID: Final = "alias_id"

CONF_ENABLED_PROVIDERS: Final = "enabled_providers"
CONF_PROVIDERS: Final = "providers"
CONF_AGENT_ID: Final = "agent_id"
CONF_CHANNEL_AGENT_ID: Final = "channel_agent_id"  # Per-channel agent override

CONF_QQ_APP_ID: Final = "qq_app_id"
CONF_QQ_CLIENT_SECRET: Final = "qq_client_secret"
QQ_TOKEN_URL: Final = "https://bots.qq.com/app/getAppAccessToken"
QQ_API_BASE: Final = "https://api.sgroup.qq.com"

CONF_WECHAT_TOKEN: Final = "wechat_token"
CONF_WECHAT_ACCOUNT_ID: Final = "wechat_account_id"
CONF_WECHAT_USER_ID: Final = "wechat_user_id"
CONF_WECHAT_BASE_URL: Final = "wechat_base_url"
CONF_WECHAT_SYNC_BUF: Final = "wechat_sync_buf"
WECHAT_DEFAULT_BASE_URL: Final = "https://ilinkai.weixin.qq.com"
WECHAT_CDN_BASE_URL: Final = "https://c2cwxappimg.weixin.qq.com"

CONF_FEISHU_APP_ID: Final = "app_id"
CONF_FEISHU_APP_SECRET: Final = "app_secret"
CONF_FEISHU_VERIFICATION_TOKEN: Final = "verification_token"
FEISHU_TOKEN_URL: Final = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

CONF_WECOM_BOT_ID: Final = "bot_id"
CONF_WECOM_SECRET: Final = "secret"
WECOM_WS_URL: Final = "wss://openws.work.weixin.qq.com"

CONF_DINGTALK_CLIENT_ID: Final = "dingtalk_client_id"
CONF_DINGTALK_CLIENT_SECRET: Final = "dingtalk_client_secret"
DINGTALK_OAUTH_URL: Final = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
DINGTALK_API_BASE: Final = "https://api.dingtalk.com"
DINGTALK_OAPI_BASE: Final = "https://oapi.dingtalk.com"

CONF_XIAOYI_AK: Final = "xiaoyi_ak"
CONF_XIAOYI_SK: Final = "xiaoyi_sk"
CONF_XIAOYI_AGENT_ID: Final = "xiaoyi_agent_id"
CONF_XIAOYI_WS_URL_1: Final = "xiaoyi_ws_url_1"
CONF_XIAOYI_WS_URL_2: Final = "xiaoyi_ws_url_2"
CONF_XIAOYI_API_ID: Final = "xiaoyi_api_id"
CONF_XIAOYI_PUSH_ID: Final = "xiaoyi_push_id"
XIAOYI_DEFAULT_WS_URL_1: Final = "wss://hag.cloud.huawei.com/openclaw/v1/ws/link"
XIAOYI_DEFAULT_WS_URL_2: Final = "wss://116.63.174.231/openclaw/v1/ws/link"

SERVICE_SEND_MESSAGE: Final = "send_message"

# agent_mail 专用服务
SERVICE_LIST_MESSAGES: Final = "list_messages"
SERVICE_READ_MESSAGE: Final = "read_message"
SERVICE_SEARCH_MESSAGES: Final = "search_messages"
SERVICE_REPLY_MESSAGE: Final = "reply_message"
SERVICE_FORWARD_MESSAGE: Final = "forward_message"
SERVICE_DELETE_MESSAGE: Final = "delete_message"

# agent_mail 服务字段
ATTR_MESSAGE_ID: Final = "message_id"
ATTR_QUERY: Final = "query"
ATTR_SEARCH_IN: Final = "search_in"
ATTR_FOLDER: Final = "folder"
ATTR_LIMIT: Final = "limit"
ATTR_CURSOR: Final = "cursor"
ATTR_CC: Final = "cc"
ATTR_BCC: Final = "bcc"
ATTR_SUBJECT: Final = "subject"
ATTR_BODY_FORMAT: Final = "body_format"
ATTR_REPLY_ALL: Final = "reply_all"
ATTR_INCLUDE_ATTACHMENTS: Final = "include_attachments"
ATTR_PERMANENT: Final = "permanent"

MAIL_FOLDERS: Final = ("inbox", "sent", "trash", "spam")
MAIL_SEARCH_IN: Final = ("SEARCH_IN_ALL", "SEARCH_IN_SUBJECT", "SEARCH_IN_CONTENT")

ATTR_PROVIDER: Final = "provider"
ATTR_TARGET: Final = "target"
ATTR_MESSAGE: Final = "message"
ATTR_TEXT: Final = "text"
ATTR_TARGET_TYPE: Final = "target_type"
ATTR_CHANNEL: Final = "channel"
ATTR_WECHAT_ACCOUNT_ID: Final = "wechat_account_id"
ATTR_CAMERA_ENTITY: Final = "camera_entity"
ATTR_FILE_PATH: Final = "file_path"
ATTR_FILE_URL: Final = "file_url"
ATTR_FILE_NAME: Final = "file_name"
ATTR_MEDIA_TYPE: Final = "media_type"
ATTR_TTS_TEXT: Final = "tts_text"
ATTR_MESSAGE_FORMAT: Final = "message_format"
ATTR_APPROVAL_ID: Final = "approval_id"
ATTR_RECORD_DURATION: Final = "record_duration"
ATTR_LOOKBACK: Final = "lookback"
ATTR_GIF_FPS: Final = "gif_fps"
ATTR_CARD_JSON: Final = "card_json"
ATTR_CARD_TITLE: Final = "card_title"
ATTR_CARD_CONTENT: Final = "card_content"
ATTR_CARD_BUTTONS: Final = "card_buttons"
DEFAULT_VIDEO_RECORD_DURATION: Final = 10
DEFAULT_GIF_DURATION: Final = 3

DEFAULT_FEISHU_TARGET_TYPE: Final = "chat_id"

CHANNEL_FEISHU_CHAT_ID: Final = "feishu/chat_id"
CHANNEL_FEISHU_OPEN_ID: Final = "feishu/open_id"
CHANNEL_WECOM_CHATID: Final = "wecom/chatid"
CHANNEL_QQ_USER: Final = "qq/user"
CHANNEL_QQ_GROUP: Final = "qq/group"
CHANNEL_QQ_CHANNEL: Final = "qq/channel"
CHANNEL_DINGTALK_USER: Final = "dingtalk/user"
CHANNEL_DINGTALK_GROUP: Final = "dingtalk/group"
CHANNEL_WECHAT_USER_ID: Final = "wechat/user_id"
CHANNEL_AGENT_MAIL: Final = "agent_mail"

CHANNEL_OPTIONS: Final = (
    CHANNEL_FEISHU_CHAT_ID,
    CHANNEL_FEISHU_OPEN_ID,
    CHANNEL_WECOM_CHATID,
    CHANNEL_QQ_USER,
    CHANNEL_QQ_GROUP,
    CHANNEL_QQ_CHANNEL,
    CHANNEL_DINGTALK_USER,
    CHANNEL_DINGTALK_GROUP,
    CHANNEL_WECHAT_USER_ID,
    CHANNEL_AGENT_MAIL,
)