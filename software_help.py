"""Deterministic, zero-token help for every user-facing Gallery workflow."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from knowledge_catalog import KnowledgeCatalog, get_knowledge_catalog


@dataclass(frozen=True)
class HelpTopic:
    id: str
    title: str
    page: str
    signals: tuple[str, ...]
    answer: str


TOPICS = (
    HelpTopic(
        "configuration",
        "统一配置、API 与账号",
        "/settings",
        ("配置", "api", "key", "token", "账号", "密码", "模型", "中转站", "grok", "novelai", "pixiv登录"),
        "把 API Base、模型、Key、NAI Token 或 Pixiv 账号资料直接交给小镜即可。小镜会先脱敏识别、列出将修改的项目并请求确认；确认后写入统一配置。Key、Token 和密码不会写进聊天记录。通行密钥、验证码或站点风控仍需要你在弹出的登录页完成。也可在“设置”页一次配置，全局使用。",
    ),
    HelpTopic(
        "gallery",
        "图库搜索与作品详情",
        "/",
        ("图库", "搜索", "筛选", "标签", "prompt", "作品详情", "收藏", "三图库"),
        "在“图库”用标题、标签或 Prompt 搜索；高级筛选可限制时间和排序。点作品进入详情可查看全部图片与本地 Prompt。网站、法典、Q 群作品使用独立图库身份，不会串号。搜索和查看只读本地数据，不消耗 Token。",
    ),
    HelpTopic(
        "queue",
        "待生成队列",
        "/queue",
        ("待生成", "队列", "清单", "加入列表", "移出列表"),
        "在图库或详情页把作品加入“待生成”，再到工作台批量处理。小镜也能按本地标签筛选后加入或移出队列；修改前会给出目标数量并请求确认。整理队列本身不识图、不消耗 NAI。",
    ),
    HelpTopic(
        "remix",
        "换角色与换画风",
        "/studio",
        ("换画风", "画风", "换角", "替换角色", "角色替换", "remix", "批量换"),
        "在“工作台”选择作品、页面和预设即可换角或换画风；也可以直接告诉小镜目标角色/画风、作品范围和每项张数。预检复用手动工具的同一配方链，不会调用识图；只有确认生成后才会消耗 NAI。批量任务会显示逐项进度、失败原因和交付报告。",
    ),
    HelpTopic(
        "director",
        "NAI 批量导演",
        "/director",
        ("批量导演", "导演工具", "director", "去背景", "线稿", "草图", "上色", "表情", "情绪", "去杂乱"),
        "“批量导演”是独立桌面功能。生成结果默认和起号流水线一样按系列显示：普通点击选整组，Ctrl+点击可增加或取消多个系列；切到“单张”可逐图挑选。网站、法典、Q 群三图库仍按精确图片身份选择。单次最多 40 张，超过上限的系列会提示切到单张挑选。选好后先点“零费用预检”核对来源、预计调用和输出数；预览不会请求 NAI。真正执行前必须勾选计费确认，完成后保留进度和交付报告。",
    ),
    HelpTopic(
        "generation",
        "生图与批量生成",
        "/studio",
        ("生图", "生成图片", "批量生成", "steps", "scale", "sampler", "尺寸", "种子"),
        "在“工作台”设置尺寸、steps、scale、sampler、seed 和每作品张数。小镜可直接复用图库已有 Prompt/标签组批，不需要先识图。真正提交 NAI 前必须确认；完成后任务中心会报告成功、失败和费用记录，失败或未完成项可以单独重试。",
    ),
    HelpTopic(
        "generated",
        "生成结果与回收站",
        "/generated",
        ("生成结果", "生成图库", "回收站", "删除图片", "恢复图片", "重试失败"),
        "“生成结果”按来源系列整理图片并显示生成参数。删除会先进入 30 天回收站，15 秒内可立即撤销；恢复时不会覆盖同名文件。失败任务可只重试失败和未完成项，成功结果不会重复生成。",
    ),
    HelpTopic(
        "pipeline",
        "后处理",
        "/pipeline",
        ("后处理", "超分", "打码", "元数据", "审核", "final"),
        "后处理按全局设置补跑超分、必要打码和元数据清理；已经完成的步骤可跳过。投稿只使用核验后的 final 文件。小镜能执行并跟踪后处理，但真实上传仍停在投稿页由你核对。",
    ),
    HelpTopic(
        "pixiv",
        "Pixiv 投稿准备",
        "/pixiv",
        ("pixiv", "投稿", "发布", "上传", "标题", "简介", "分级", "r18"),
        "在“发布”选择生成系列，准备标题、简介、标签、分级和后处理结果。小镜可以整理完整投稿草稿，但不会替你跳过最终确认；实际上传固定使用当前高亮账号，并在提交前显示账号、图片数、分级和处理状态。",
    ),
    HelpTopic(
        "butler",
        "智能管家与任务中心",
        "/butler",
        ("小镜", "智能管家", "任务中心", "进度", "聊天记录", "常用任务", "执行报告"),
        "问小镜问题时会直接回答并保留聊天记录，不会创建任务或执行工具；只有明确使用命令式说法交代操作时才进入任务流。写入、生成、账号配置和投稿准备会按安全边界确认。任务中心实时显示真正任务的当前步骤、下一步、预计时间和结果，完成后自动写交付报告。",
    ),
    HelpTopic(
        "vision",
        "识图与图库体检",
        "/butler",
        ("识图", "看图", "哪张好看", "比较图片", "图片评价", "图库体检", "质量检查"),
        "默认图库体检只做本地技术检查，不调用识图。只有你明确要求判断画面、比较哪张更好看或分析图片时，才会发送压缩缩略图给已配置的视觉模型；上游拒绝时小镜会保留本地报告并明确说明。",
    ),
    HelpTopic(
        "operations",
        "运行状态与排错",
        "/ops",
        ("运行状态", "系统状态", "为什么失败", "报错", "排错", "健康", "日志", "运行情况"),
        "先在“工具 → 运行状态”查看服务、数据库、生成、后处理和采集状态。也可以把报错原文交给小镜；小镜会结合本地任务证据解释失败位置，不会把未知外部结果说成成功，也不会自动重放可能已经扣费的操作。",
    ),
)

def _knowledge_page(source: str) -> str:
    folded = str(source or "").casefold()
    if "nai" in folded or "anima" in folded or "reference" in folded:
        return "/references"
    if "pixiv" in folded or "publish" in folded:
        return "/pixiv"
    if "studio" in folded or "remix" in folded:
        return "/studio"
    return "/butler"


def looks_like_help_question(value: Any) -> bool:
    text = "".join(str(value or "").lower().split())
    if not text or len(text) > 500:
        return False
    question_signals = ("怎么", "如何", "在哪", "哪里", "什么是", "为什么", "能不能", "会不会", "可以吗", "教程", "怎么用", "使用方法", "操作方法", "帮助")
    return any(signal in text for signal in question_signals)


def looks_like_question(value: Any) -> bool:
    """Recognize answer-seeking language before it can enter the task planner."""

    text = " ".join(str(value or "").strip().split()).casefold()
    if not text or len(text) > 2_000:
        return False
    compact = text.replace(" ", "")
    if "?" in text or "？" in text:
        return True
    signals = (
        "怎么", "如何", "为什么", "什么", "哪些", "哪个", "哪张", "哪里", "在哪",
        "是否", "能不能", "能否", "可不可以", "会不会", "有没有", "多少", "怎么样",
        "怎样", "怎么看", "解释", "说明一下", "介绍一下", "告诉我", "给我建议", "评价一下",
        "what ", "why ", "how ", "where ", "which ", "can ", "could ", "should ",
    )
    if any(signal in compact for signal in signals):
        return True
    return compact.endswith(("吗", "呢", "么"))


def answer_software_question(
    value: Any,
    *,
    knowledge_catalog: KnowledgeCatalog | None = None,
) -> dict[str, Any]:
    question = " ".join(str(value or "").strip().split())
    if not question:
        raise ValueError("请输入软件使用问题")
    folded = question.casefold()
    scored = [
        (sum(2 if signal in folded else 0 for signal in topic.signals), index, topic)
        for index, topic in enumerate(TOPICS)
    ]
    score, _, topic = max(scored, key=lambda row: (row[0], -row[1]))
    if score <= 0:
        try:
            knowledge = (knowledge_catalog or get_knowledge_catalog()).search(
                question,
                limit=3,
                char_budget=1_200,
            )
        except (OSError, sqlite3.Error):
            knowledge = {"items": []}
        items = list(knowledge.get("items") or [])
        if items:
            sources = list(dict.fromkeys(str(item.get("source") or "") for item in items if item.get("source")))
            first = items[0]
            heading = str(first.get("heading") or first.get("title") or "本地说明").strip()
            excerpt = str(first.get("text") or "").strip()
            return {
                "ok": True,
                "topic": "knowledge",
                "title": heading,
                "page": _knowledge_page(str(first.get("source") or "")),
                "answer": f"{heading}：{excerpt}" if heading else excerpt,
                "provider": "local_knowledge",
                "model_calls": 0,
                "sources": sources,
            }
        return {
            "ok": True,
            "topic": "overview",
            "title": "软件使用导航",
            "page": "/butler",
            "answer": (
                "我可以回答图库搜索、收藏与待生成、换角/换画风、生图参数、生成结果、"
                "后处理、Pixiv 投稿、账号/API 配置、任务进度、识图和运行排错。"
                "你可以直接说“我现在看到什么、想完成什么”，我会给出具体入口、步骤、"
                "是否消耗 Token 以及哪些动作需要确认。"
            ),
            "provider": "local_topic",
            "model_calls": 0,
            "sources": [],
        }
    return {
        "ok": True,
        "topic": topic.id,
        "title": topic.title,
        "page": topic.page,
        "answer": topic.answer,
        "provider": "local_topic",
        "model_calls": 0,
        "sources": [],
    }


def catalogue() -> list[dict[str, str]]:
    return [{"id": item.id, "title": item.title, "page": item.page} for item in TOPICS]
