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
