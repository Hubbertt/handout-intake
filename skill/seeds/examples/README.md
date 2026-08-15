# seeds/examples · 参照样例

来自化学册(g08 沪科版专题3-4)的两份**参照件**,是方法层的种子,不是任何册的私有规则:

| 文件 | 谁读 | 用途 |
|---|---|---|
| `private-spec-mapping.chemistry-g08.v1.json` | `s4-merge-mapping` | 私有规范映射的**结构参照**——本册的映射与它并置比对,查缺的键 |
| `audit-matrix.chemistry-g08.v1.json` | `s2-matrix` | 审计矩阵的参照——对象类 × 视角,空白格是工作清单 |

★2026-08-15 全新安装抓出:这两份原先由册级绑定**指向生产线目录**(`teaching-materials-production-pipeline/…`),
每一次「全新安装」都通过拷来的绑定悄悄继承了它们——真正陌生的机器上两者都不存在,链在第 2、4 步就断。
它们是种子,随 skill 走;绑定模板现在指向这里。
| `handout-carve.chemistry-g08.v1.json` | `s1b-fingerprint` | 切分 schema 的参照(化学册)——本册 schema 与它比对 |

## schema 基底与偏离(2026-08-16 起)

| 文件 | 是什么 |
|---|---|
| `../schema-base/schema-base.v1.json` | 公有基底:15 个键的骨架 + 真通用的默认(2 个角色、7 个诊断)。**不是两册的并集** |
| `schema.deviation.physics-g08-summer.json` | 物理的偏离(由全量 schema 拆出,拆合无损) |
| `schema.deviation.chemistry-g08.json` | 化学的偏离(拆合无损;缺 id/status/hierarchy/fingerprint,基底会拒绝合成直到补上——早期 schema 没这些约定,基底如实指出) |

合成/拆分/自证:`skill/method/scripts/compose_schema.py compose|split|check`。
新一册:拷一份最像的偏离,改角色清单/正则/层级/栏目名;缺 required 键合成会点名。
