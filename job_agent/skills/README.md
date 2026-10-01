# Skills

这里存放应用内部可调用的能力模块。每项已启用能力包含：

- 一个静态 `SkillSpec`，声明输入限制、输出 Token 上限、超时和外发数据；
- 该能力专用的系统提示词；
- 业务模块中的确定性校验和人工确认流程。

`registry.py` 只注册经过代码审查的模块，不扫描目录、不动态执行用户文件。新增能力时，先实现业务入口和校验，再加入显式注册表。

当前能力：

- `job_extraction`：岗位、JD、招聘日期和进度分拣；
- `candidate_extraction`：只分析求职者简历资料；
- `resume_material_matching`：按需推荐档案中已有的技能和项目，并核对 JD 原文依据；
- `intake_extraction`：旧版混合求职信息分拣，保留供兼容调用；
- `resume_generation`：基于证据的岗位定制简历。

本地文档能力在 `documents.py` 中显式登记：Word/PDF 文字导入，以及定制简历 DOCX/PDF 导出。文件先在本机处理，只有用户点击对应的 AI 分析、推荐或生成按钮后，相应输入文字才会发送给模型。
