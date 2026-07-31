"""SqlGlot 编译器可行性验证 v2 —— 修复列归属解析 + 别名列跳过。

v1 暴露的难点用编译器标准手法解决：
  - 两遍遍历：pass1 收集 alias→ObjectType 映射（不改写），pass2 改写
  - 别名列跳过：SELECT ... AS xxx 的 xxx 是输出别名，不校验
  - 列归属：优先用 alias 映射，无 alias 时用单表 fallback
"""

from __future__ import annotations

import itertools

import sqlglot
from sqlglot import exp


class FakeSchema:
    def __init__(self) -> None:
        self.object_types: dict[str, str] = {
            "Order": "idx_airline__order",
            "Customer": "idx_airline__customer",
            "Product": "idx_airline__product",
        }
        self.properties: dict[str, dict[str, str]] = {
            "Order": {
                "orderNo": "order_no",
                "amount": "amount",
                "status": "status",
                "customerId": "customer_id",
                "productId": "product_id",
                "region": "region",
                "createdAt": "created_at",
            },
            "Customer": {"customerId": "customer_id", "name": "name", "region": "region"},
            "Product": {"productId": "product_id", "productName": "product_name", "price": "price"},
        }
        self.links: set[tuple[str, str]] = {
            ("Order", "Customer"),
            ("Customer", "Order"),
            ("Order", "Product"),
            ("Product", "Order"),
        }

    def physical_table(self, ot_api: str) -> str:
        if ot_api not in self.object_types:
            raise ValueError(f"INVALID_TABLE: ObjectType {ot_api!r} 未定义")
        return self.object_types[ot_api]

    def has_link(self, a: str, b: str) -> bool:
        return (a, b) in self.links


class CompilerV2:
    def __init__(self, schema: FakeSchema) -> None:
        self.schema = schema
        self.params: list[object] = []
        # alias → ObjectType api_name（pass1 填充，pass2 使用）
        self.alias_map: dict[str, str] = {}
        # 物理表名 → ObjectType（反查用）
        self.phys_to_ot: dict[str, str] = {v: k for k, v in schema.object_types.items()}

    def compile(self, logical_sql: str, dialect: str) -> str:
        self.params = []
        self.alias_map = {}
        ast = sqlglot.parse_one(logical_sql, read="mysql")
        # ── Pass 1: 收集 alias → ObjectType 映射（不改写）──
        for t in ast.find_all(exp.Table):
            ot = t.name if t.name in self.schema.object_types else self.phys_to_ot.get(t.name)
            if ot:
                # alias 优先，否则用 ObjectType 名本身
                key = t.alias or t.name
                self.alias_map[key] = ot
        # ── Pass 1.5: 收集 SELECT 输出别名（这些不该当输入列校验）──
        output_aliases: set[str] = set()
        for proj in ast.find_all(exp.Alias):
            if proj.alias:
                output_aliases.add(proj.alias)
        # 也收集无 AS 但有隐式别名的（窗口函数等）
        for node in ast.walk():
            if isinstance(node, exp.Alias):
                output_aliases.add(node.alias)
        # ── Pass 2: 改写 + 校验 ──
        ast = self._rewrite(ast, dialect, output_aliases)
        return ast.sql(dialect=dialect)

    def _rewrite(self, node: exp.Expression, dialect: str, out_aliases: set[str]) -> exp.Expression:
        # 1. 表节点
        if isinstance(node, exp.Table):
            ot = node.name if node.name in self.schema.object_types else self.phys_to_ot.get(node.name)
            if not ot:
                raise ValueError(f"INVALID_TABLE: ObjectType {node.name!r} 未定义")
            physical = self.schema.physical_table(ot)
            # 保留 alias 不变（alias 是 LLM 写的，列前缀靠它解析）
            node.set("this", exp.to_identifier(physical, quoted=False))
            return node

        # 2. 列节点（必须在表改写前处理列，否则 from 里的表名已变物理名）
        # 实际上递归是深度优先，Select 的 from 先于 expressions 处理，所以
        # _resolve_owner 必须兼容物理名（已通过 phys_to_ot 处理）

        # 2. 列节点
        if isinstance(node, exp.Column):
            col_api = node.name
            # 跳过输出别名（SELECT ... AS rn 后，外层 WHERE rn<=3 里的 rn 是别名）
            # 注意：只有当该列无表前缀且本层 SELECT 无法解析时才当别名跳过
            if col_api in out_aliases and not node.table:
                # 但若能解析到真实 ObjectType 属性，优先按属性处理（避免别名遮蔽真实列）
                owner = self._resolve_owner(node)
                if owner is None:
                    return node
                props = self.schema.properties.get(owner, {})
                if col_api not in props:
                    return node
                # fall through to normal column rewrite
            owner_ot = self._resolve_owner(node)
            if owner_ot is None:
                raise ValueError(f"CANNOT_RESOLVE_COLUMN_OWNER: {col_api!r} (alias_map={self.alias_map})")
            props = self.schema.properties.get(owner_ot, {})
            if col_api not in props:
                raise ValueError(f"INVALID_COLUMN: Property {col_api!r} 不属于 ObjectType {owner_ot}")
            node.set("this", exp.to_identifier(props[col_api], quoted=False))
            return node

        # 3. JOIN 校验
        if isinstance(node, exp.Join):
            self._validate_join(node)

        # 4. 字面量 → 参数化占位
        if isinstance(node, exp.Literal) and not isinstance(node.parent, (exp.Identifier,)):
            self.params.append(node.this)
            return exp.Placeholder()

        # 递归
        for key, child in list(node.args.items()):
            if isinstance(child, list):
                node.set(key, [self._rewrite(c, dialect, out_aliases) for c in child if isinstance(c, exp.Expression)])
            elif isinstance(child, exp.Expression):
                node.set(key, self._rewrite(child, dialect, out_aliases))
        return node

    def _resolve_owner(self, col: exp.Column) -> str | None:
        """列归属解析：alias 前缀 → alias_map；无前缀 → 单表/子查询上下文 fallback。"""
        tbl = col.table
        if tbl:
            # 直接命中 alias_map
            if tbl in self.alias_map:
                return self.alias_map[tbl]
            # 物理表名反查
            if tbl in self.phys_to_ot:
                return self.phys_to_ot[tbl]
            return None
        # 无前缀：找该列所在的最内层 SELECT 的 FROM/JOIN 表
        select = col.find_ancestor(exp.Select)
        if select:
            ots: set[str] = set()
            # SqlGlot 30.x: key 是 from_ 不是 from
            from_clause = select.args.get("from") or select.args.get("from_")
            if from_clause:
                for t in from_clause.find_all(exp.Table):
                    ot = t.name if t.name in self.schema.object_types else self.phys_to_ot.get(t.name)
                    if ot:
                        ots.add(ot)
            for j in select.args.get("joins", []) or []:
                for t in j.find_all(exp.Table):
                    ot = t.name if t.name in self.schema.object_types else self.phys_to_ot.get(t.name)
                    if ot:
                        ots.add(ot)
            if len(ots) == 1:
                return next(iter(ots))
        return None

    def _validate_join(self, join: exp.Join) -> None:
        select = join.find_ancestor(exp.Select)
        if not select:
            return
        ots: set[str] = set()
        for t in select.find_all(exp.Table):
            ot = t.name if t.name in self.schema.object_types else self.phys_to_ot.get(t.name)
            if ot:
                ots.add(ot)
        if len(ots) < 2:
            return
        for a, b in itertools.combinations(ots, 2):
            if self.schema.has_link(a, b):
                return
        raise ValueError(f"INVALID_JOIN: ObjectType 组合 {ots} 之间未定义 LinkType")


# ── 测试 ────────────────────────────────────────────────────────────────

schema = FakeSchema()


def run(name: str, sql: str, expect_error: str | None = None) -> bool:
    print(f"\n{'=' * 70}\n{name}\n逻辑SQL: {sql[:120]}")
    c = CompilerV2(schema)
    try:
        doris = c.compile(sql, "doris")
        trino = c.compile(sql, "trino")
        print(f"  Doris: {doris}")
        print(f"  Trino: {trino}")
        print(f"  params: {c.params}")
        if expect_error:
            print(f"  ✗ 预期 {expect_error} 但成功")
            return False
        return True
    except ValueError as e:
        if expect_error and expect_error in str(e):
            print(f"  ✓ 拦截: {e}")
            return True
        print(f"  ✗ 异常: {e}")
        return False


r = []
r.append(
    run(
        "1. 单表过滤+排序分页",
        "SELECT orderNo, amount FROM Order WHERE status = 'OVERDUE' AND amount > 100000 ORDER BY amount DESC LIMIT 10 OFFSET 20",
    )
)
r.append(
    run(
        "2. 多表JOIN（Order↔Customer）",
        "SELECT o.orderNo, c.name FROM Order o JOIN Customer c ON o.customerId = c.customerId WHERE c.region = 'EAST'",
    )
)
r.append(
    run(
        "3. 子查询",
        "SELECT orderNo, amount FROM Order WHERE amount > (SELECT AVG(amount) FROM Order WHERE region = 'EAST')",
    )
)
r.append(
    run(
        "4. 窗口函数（每区域Top3）",
        "SELECT orderNo, region, amount FROM (SELECT orderNo, region, amount, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) AS rn FROM Order) t WHERE rn <= 3",
    )
)
r.append(run("5. 自定义算式", "SELECT orderNo, amount, amount * 0.8 AS discounted FROM Order WHERE status = 'PAID'"))
r.append(
    run(
        "6. 聚合+HAVING",
        "SELECT region, SUM(amount) AS total, COUNT(*) AS cnt FROM Order WHERE status = 'PAID' GROUP BY region HAVING SUM(amount) > 1000000 ORDER BY total DESC",
    )
)
r.append(
    run(
        "7. CTE+UNION",
        "WITH vip AS (SELECT customerId FROM Customer WHERE region = 'EAST'), overdue AS (SELECT customerId FROM Order WHERE status = 'OVERDUE') SELECT customerId FROM vip UNION SELECT customerId FROM overdue",
    )
)
r.append(run("8. 非法表名", "SELECT * FROM Supplier WHERE id = 1", expect_error="INVALID_TABLE"))
r.append(run("9. 非法列名", "SELECT orderNo, color FROM Order WHERE status = 'PAID'", expect_error="INVALID_COLUMN"))
r.append(
    run(
        "10. 非法JOIN",
        "SELECT p.productName, c.name FROM Product p JOIN Customer c ON p.productId = c.customerId",
        expect_error="INVALID_JOIN",
    )
)
r.append(
    run(
        "11. 三表JOIN",
        "SELECT o.orderNo, c.name, p.productName FROM Order o JOIN Customer c ON o.customerId = c.customerId JOIN Product p ON o.productId = p.productId WHERE o.status = 'PAID'",
    )
)
r.append(
    run(
        "12. SQL注入（多语句）",
        "SELECT orderNo FROM Order WHERE status = 'PAID' OR 1=1; DROP TABLE x; --'",
        expect_error="INVALID_TABLE",
    )
)
# 额外难点场景
r.append(
    run(
        "13. 自连接（同一ObjectType两个alias）",
        "SELECT a.orderNo, b.orderNo AS related FROM Order a JOIN Order b ON a.customerId = b.customerId WHERE a.status = 'PAID'",
    )
)
r.append(
    run(
        "14. 嵌套子查询+JOIN混合",
        "SELECT o.orderNo FROM Order o WHERE o.customerId IN (SELECT customerId FROM Customer WHERE region = 'EAST') AND o.amount > 5000",
    )
)
r.append(
    run(
        "15. CASE表达式",
        "SELECT orderNo, CASE WHEN amount > 10000 THEN 'HIGH' WHEN amount > 1000 THEN 'MID' ELSE 'LOW' END AS tier FROM Order",
    )
)

print(f"\n{'=' * 70}\n结果: {sum(r)}/{len(r)} 通过")
for i, x in enumerate(r, 1):
    print(f"  {i}: {'✓' if x else '✗'}")
