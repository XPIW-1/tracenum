# 溯数风险登记表

每一类已知隐患对应一个**自动检查**。发布前运行 `python scripts/release_check.py --python <模型环境>`，任何一项 FAIL 都不发布。

**规则：发现新的问题类别时，先把它写成一条检查规则（corpus/v3/lint.py、backend/answer_check.py 或 scripts/release_check.py）并补测试，确认检查能在旧数据上报错，再修数据或代码。** 只修这一次而不加规则，不算修完。

最后复核：2026-11-21

## A. 证据可信度

| # | 隐患 | 控制措施 | 检查位置 | 状态 |
|---|---|---|---|---|
| A1 | 系统占位词、内部枚举混进模型读到的证据，被当成年报原文引用（2026-11 发现：待核验 20,328 片、other 27,371 片、unknown 8,595 片） | 片段契约：只允许定位行、带页码的逐字原文行、带固定标记「〔系统注：非年报原文〕」的系统注；未知值不写 | `corpus/v3/lint.py`；`build_index.py` 有违规拒绝建索引 | 已控 |
| A2 | 片段正文与 PDF 不一致（解析错位、拼接） | 每个表格单元格、正文片段、引用行都必须能在其来源页原文中找到 | `lint.py` | 已控 |
| A3 | 主报表金额表缺单位 | 先用表格自带单位行，再用财务报表部分的全文单位声明（如比亚迪 2023 第153页），都没有则写系统注；主报表覆盖率要求 100% | `lint.py` 覆盖率；`parse.py` 预扫描 | 已控（2,221/2,221） |
| A4 | 回答复述系统注或占位词 | 回答检查，`system_note_leak` 为阻断级：精选回放不发布，实时回答显示提示 | `backend/answer_check.py`；`release_check.py featured` | 已控 |
| A5 | 回答与冻结流水线的核验器争辩（`UNSUPPORTED`、`verified_operands` 等，见于 431 题中约 16% 的回答） | v2 不可改，所以在产品层把相关句子折叠进「查看修正过程」；发布检查只看访客默认可见的文字 | `web/src/answerRevision.js foldAnswer`；`release_check.py` | 已控（可见部分 0 处） |
| A6 | 引号里的话不是原文 | 以契约保证的「原文部分」为参照（系统注不算依据）；警告级，发布前人工看 | `answer_check.py unsupported_quote` | 警告 |
| A7 | 引用页不在证据里 | 警告级 | `answer_check.py page_not_in_evidence` | 警告 |

## B. 评测可信度

| # | 隐患 | 控制措施 | 检查位置 | 状态 |
|---|---|---|---|---|
| B1 | 评分过宽：答案里任一数字命中即算对 | 严格复评：只认结论里作为结果的金额；每次同时报告两种评分 | `eval/strict_grade.py`；`release_check.py eval` | 已控（430/431 两种评分一致） |
| B2 | 评测结果存的答案被截断（曾截到 600 字），漏数系统词 | 保存全文 | `eval/run_numeric.py` | 已修 |
| B3 | 断点续跑把旧语料的结果混进新结果 | 每行记录 chunks sha256；续跑只保留同版本的行；发布检查要求全部行来自当前语料 | `run_numeric.py`；`release_check.py eval` | 已控 |
| B4 | 精选回放只录一次，语料或模型变了不再校验 | 每条回放在 `provenance.json` 记录语料版本、代码版本、模型指纹和回答检查结果；语料变了即判过期 | `scripts/record_v3_featured.py`；`release_check.py featured` | 已控 |
| B5 | 评测只覆盖主报表数值 | 计划补约 30 道陷阱题（缺单位、跨页、追溯调整、同值异科目、应拒答、引文忠实） | — | 未做 |

## C. 构建与发布

| # | 隐患 | 控制措施 | 检查位置 | 状态 |
|---|---|---|---|---|
| C1 | 索引与片段不一致但条数相同，旧校验只比条数（本次实测：旧方法报 36/36 完整，实际 0/36） | 比较集合元数据中的 chunks sha256 | `build_index.py --verify`；`release_check.py index` | 已控 |
| C2 | CI 只跑访问控制测试 | CI 增加语料契约、解析规则、Gold 工具函数和前端折叠测试；需要模型环境的测试由发布检查跑 | `.github/workflows/ci.yml`；`release_check.py tests` | 已控 |
| C3 | 代码只在笔记本上 | 发布检查要求工作区干净、HEAD 已在远端 | `release_check.py git` | 已控 |
| C4 | 仓库里混入密钥 | 发布检查扫描已跟踪文件 | `release_check.py secrets` | 已控 |
| C5 | PDF 和索引只有笔记本和服务器两份 | 登记表保存巨潮下载地址和 sha256，PDF 可重新下载并校验；索引可由片段重建（约 5 小时） | `corpus/registry.json`、`corpus/fetch_reports.py` | 可恢复，未做异地备份 |
| C6 | 生成模型被服务方静默更新（指纹已从 e72d3c7f 变为 bc19ba97） | 指纹写入每条评测行、每条回放和发布清单；指纹变化时重跑数值评测再发布 | `release_check.py` 清单 | 记录，变化时需人工判断 |

## D. 线上运行

| # | 隐患 | 控制措施 | 检查位置 | 状态 |
|---|---|---|---|---|
| D1 | 每月花费低估：重排器按 0 元计，结算时也漏算重排调用 | 重排按 0.01 元/次上限计入预留和结算；官方价格未确认，需对照硅基流动账单下调 | `backend/billing.py`；`server.py` | 已控，待对账 |
| D2 | 运行日志和回放含访客问题，无保留期 | 生产环境保留 30 天后删除，精选回放不删；本地和测试默认不删 | `server.py prune_logs`；`Dockerfile LOG_RETENTION_DAYS` | 已控 |
| D3 | `/api/health` 永远返回正常 | 新增 `/api/ready`：额度库、磁盘余量、索引集合数、模型加载；对外只报哪项失败 | `server.py` | 已控，无自动告警 |
| D4 | 无 CSP 头 | 先上 Report-Only，确认两个站点控制台无违规后改为强制 | `deploy/Caddyfile` | 进行中 |
| D5 | 实时问答的合规（生成式 AI 服务备案） | 实时提问保持关闭（LIVE_ENABLED=0），只对私密链接开放；是否咨询网信部门由项目负责人决定 | `.env` | 待负责人决定 |
| D6 | 访问 cookie 设在父域 | HttpOnly、SameSite=Lax；两个子站都由本人控制 | `backend/access.py` | 接受 |

## 不做的事

- 每次提交都重建 5 小时的索引、重跑 431 题：只在语料、检索或模型指纹变化时做。
- 自建监控告警或多副本高可用：实时提问默认关闭，`/api/ready` 加发布检查足够。
- 逐条人工重签所有回答：用自动检查加抽样签核。
