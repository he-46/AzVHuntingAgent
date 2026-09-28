"""Evidence-grounded resume tailoring skill."""

from job_agent.skills.base import InputLimit, SkillSpec


SPEC = SkillSpec(
    key="resume_generation",
    title="岗位定制简历",
    description="按照目标 JD 重排和改写基础简历，并列出证据缺口和面试准备主题。",
    operation="resume_generation",
    input_limits=(
        InputLimit("source_resume", "基础简历", 20_000),
        InputLimit("jd", "当前 JD", 15_000),
    ),
    max_output_tokens=4_000,
    timeout_seconds=45.0,
    external_data=("基础简历", "公司和岗位信息", "JD"),
)


SYSTEM_PROMPT = """你是求职简历编辑器。根据候选人的基础简历和目标岗位资料，生成中文 Markdown 简历草稿。
只能重组、压缩和改写基础简历中明确存在的事实。不得新增公司、项目、职责、技能、学历、证书、数字、
业绩、时间或个人信息。JD 中出现而基础简历未证明的能力，只能列入 missing_evidence，不能写进简历。
优先把与 JD 最相关的真实经历放在前面，使用简洁、具体、以行动开头的表达。保留基础简历中的联系方式，
不要猜测或补全。tailored_resume_markdown 应包含适合直接编辑的完整简历结构。
match_analysis 简明说明已有经历与 JD 的匹配点；missing_evidence 列出 JD 要求但简历没有证据的内容；
interview_focus 列出建议准备的面试主题。不要输出录取概率。"""
