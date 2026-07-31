"""SqlGlot 编译器可行性验证（text2sql 方案2 技术预研）。

验证目标：用 SqlGlot AST 改写，把 LLM 生成的"逻辑 SQL"（ObjectType 名
当表名、api_name 当列名）编译成 Doris/Trino 物理方言 SQL，并在编译期
强制三大护栏（表/列/JOIN 白名单校验）+ 参数化绑定。

覆盖 7 个复杂场景，每个都跑通才算技术可行：
  1. 单表过滤 + 排序分页（基线）
  2. 多表 JOIN（走 LinkType 校验）
  3. 子查询
  4. 窗口函数（ROW_NUMBER OVER PARTITION BY）
  5. 自定义算式（amount * 0.8）
  6. 聚合 + HAVING + GROUP BY
  7. UNION + 嵌套 CTE
  8. 参数化绑定（字面量抽到 ? 占位）
  9. 非法表名/列名/JOIN 被护栏拦截
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

# ── 模拟本体 Schema（与 Gaia 真实模型对应）──────────────────────────────


class FakeSchema:
    """模拟 OntologyService 加载的本体 Schema。"""

    def __init__(self) -> None:
        # ObjectType api_name → 物理表名（Doris idx 命名规范 v5.2）
        self.object_types: dict[str, str] = {
            "Order": "idx_airline__order",
            "Customer": "idx_airline__customer",
            "Product": "idx_airline__product",
        }
        # ObjectType → { property api_name → backing_column (snake_case) }
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
            "Customer": {
                "customerId": "customer_id",
                "name": "name",
                "region": "region",
            },
            "Product": {
                "productId": "product_id",
                "productName": "product_name",
                "price": "price",
            },
        }
        # LinkType: (source_ot, target_ot) 双向已定义
        self.links: set[tuple[str, str]] = {
            ("Order", "Customer"),
            ("Customer", "Order"),
            ("Order", "Product"),
            ("Product", "Order"),
        }

    def physical_table(self, ot_api: str) -> str:
        if ot_api not in self.object_types:
            raise ValueError(f"UNKNOWN_OBJECT_TYPE: {ot_api}")
        return self.object_types[ot_api]

    def has_link(self, a: str, b: str) -> bool:
        return (a, b) in self.links


# ── 编译器核心：AST 遍历改写 ────────────────────────────────────────────


class Compiler:
    def __init__(self, schema: FakeSchema) -> None:
        self.schema = schema
        self.params: list[object] = []

    def compile(self, logical_sql: str, dialect: str) -> str:
        self.params = []
        # LLM 写类 MySQL 语法（最通用，LLM 训练数据多）
        ast = sqlglot.parse_one(logical_sql, read="mysql")
        ast = self._rewrite(ast, dialect)
        return ast.sql(dialect=dialect)

    def _rewrite(self, node: exp.Expression, dialect: str) -> exp.Expression:
        # 1. 表节点：ObjectType 名 → 物理表名
        if isinstance(node, exp.Table):
            ot_api = node.name
            if ot_api not in self.schema.object_types:
                raise ValueError(f"INVALID_TABLE: ObjectType {ot_api!r} 未定义")
            physical = self.schema.physical_table(ot_api)
            node.set("this", exp.to_identifier(physical, quoted=False))
            return node

        # 2. 列节点：api_name → backing_column（需解析列属于哪个 ObjectType）
        if isinstance(node, exp.Column):
            col_api = node.name
            owner_ot = self._resolve_owner(node)
            if owner_ot is None:
                raise ValueError(f"CANNOT_RESOLVE_COLUMN_OWNER: {col_api!r}")
            props = self.schema.properties.get(owner_ot, {})
            if col_api not in props:
                raise ValueError(f"INVALID_COLUMN: Property {col_api!r} 不属于 ObjectType {owner_ot}")
            node.set("this", exp.to_identifier(props[col_api], quoted=False))
            return node

        # 3. JOIN 节点：校验表对在本体 LinkType 中（关系约束护栏）
        if isinstance(node, exp.Join):
            self._validate_join(node)

        # 4. 字面量：抽到 params，替换为占位符（参数化绑定防注入）
        if isinstance(node, exp.Literal) and not isinstance(node.parent, exp.Identifier):
            # 排除被当 identifier quote 用的 literal（如 quoted table name）
            self.params.append(node.this)
            # Doris/Trino 都支持 ? 占位
            return exp.Placeholder()

        # 递归处理所有子节点
        for key, child in list(node.args.items()):
            if isinstance(child, list):
                node.set(key, [self._rewrite(c, dialect) for c in child if isinstance(c, exp.Expression)])
            elif isinstance(child, exp.Expression):
                node.set(key, self._rewrite(child, dialect))
        return node

    def _resolve_owner(self, col: exp.Column) -> str | None:
        """解析列属于哪个 ObjectType：优先看 table alias，再 fallback 单表查询。"""
        tbl = col.table  # 如 "o" 或 "Order"
        if tbl:
            # alias → 找 FROM/JOIN 里该 alias 指向的表
            select = col.find_ancestor(exp.Select)
            if select:
                for t in select.find_all(exp.Table):
                    alias = t.alias or t.name
                    if alias == tbl and t.name in self.schema.object_types:
                        # 注意：t.name 此时可能已被前序遍历改写为物理名，
                        # 但我们改写时保留了 alias 不变，所以用 alias 匹配。
                        # 若 t.name 已是物理名，反查 object_types value。
                        ot = t.name if t.name in self.schema.object_types else self._reverse_table(t.name)
                        if ot:
                            return ot
        # 无 table 前缀：fallback 到查询里唯一的 ObjectType
        select = col.find_ancestor(exp.Select)
        if select:
            tables = [t.name for t in select.find_all(exp.Table) if t.name in self.schema.object_types]
            tables = list({t for t in tables})
            if len(tables) == 1:
                return tables[0]
            # 物理名反查
            phys = [t.name for t in select.find_all(exp.Table)]
            ots = {self._reverse_table(p) for p in phys if self._reverse_table(p)}
            if len(ots) == 1:
                return next(iter(ots))
        return None

    def _reverse_table(self, physical: str) -> str | None:
        for ot, phys in self.schema.object_types.items():
            if phys == physical:
                return ot
        return None

    def _validate_join(self, join: exp.Join) -> None:
        """校验 JOIN 的左右表对在本体 LinkType 中已定义。"""
        select = join.find_ancestor(exp.Select)
        if not select:
            return
        all_tables = list(select.find_all(exp.Table))
        if len(all_tables) < 2:
            return
        # 找到 JOIN 节点本身的表 + 它之前的表（简化：取所有表的两两组合）
        ots = set()
        for t in all_tables:
            ot = t.name if t.name in self.schema.object_types else self._reverse_table(t.name)
            if ot:
                ots.add(ot)
        # 校验：至少存在一对已定义的 LinkType
        import itertools

        for a, b in itertools.combinations(ots, 2):
            if self.schema.has_link(a, b):
                return
        raise ValueError(f"INVALID_JOIN: ObjectType 组合 {ots} 之间未定义 LinkType")


# ── 测试用例 ────────────────────────────────────────────────────────────

schema = FakeSchema()


def run_case(name: str, sql: str, expect_doris: bool = True, expect_error: str | None = None) -> bool:
    print(f"\n{'=' * 70}\n场景 {name}\n逻辑 SQL: {sql}")
    c = Compiler(schema)
    try:
        doris_sql = c.compile(sql, "doris")
        trino_sql = c.compile(sql, "trino")
        print(f"  → Doris:  {doris_sql}")
        print(f"  → Trino:  {trino_sql}")
        print(f"  → params: {c.params}")
        if expect_error:
            print(f"  ✗ 预期报错 {expect_error} 但成功了")
            return False
        return True
    except ValueError as e:
        if expect_error and expect_error in str(e):
            print(f"  ✓ 正确拦截: {e}")
            return True
        print(f"  ✗ 异常: {e}")
        return False


results = []

# 场景1: 单表过滤 + 排序分页
results.append(
    run_case(
        "1. 单表过滤+排序分页",
        "SELECT orderNo, amount FROM Order WHERE status = 'OVERDUE' AND amount > 100000 "
        "ORDER BY amount DESC LIMIT 10 OFFSET 20",
    )
)

# 场景2: 多表 JOIN（走 LinkType）
results.append(
    run_case(
        "2. 多表JOIN（Order↔Customer 已定义 LinkType）",
        "SELECT o.orderNo, c.name FROM Order o JOIN Customer c ON o.customerId = c.customerId WHERE c.region = 'EAST'",
    )
)

# 场景3: 子查询
results.append(
    run_case(
        "3. 子查询（区域平均金额以上的订单）",
        "SELECT orderNo, amount FROM Order WHERE amount > (SELECT AVG(amount) FROM Order WHERE region = 'EAST')",
    )
)

# 场景4: 窗口函数
results.append(
    run_case(
        "4. 窗口函数（每区域金额Top3）",
        "SELECT orderNo, region, amount FROM ("
        "SELECT orderNo, region, amount, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) AS rn "
        "FROM Order) t WHERE rn <= 3",
    )
)

# 场景5: 自定义算式
results.append(
    run_case(
        "5. 自定义算式（打折价 = amount * 0.8）",
        "SELECT orderNo, amount, amount * 0.8 AS discounted FROM Order WHERE status = 'PAID'",
    )
)

# 场景6: 聚合 + HAVING + GROUP BY
results.append(
    run_case(
        "6. 聚合+HAVING（区域总金额超100万的）",
        "SELECT region, SUM(amount) AS total, COUNT(*) AS cnt FROM Order "
        "WHERE status = 'PAID' GROUP BY region HAVING SUM(amount) > 1000000 ORDER BY total DESC",
    )
)

# 场景7: UNION + CTE
results.append(
    run_case(
        "7. CTE + UNION（高价值客户和逾期订单的客户并集）",
        "WITH vip AS (SELECT customerId FROM Customer WHERE region = 'EAST'), "
        "overdue AS (SELECT customerId FROM Order WHERE status = 'OVERDUE') "
        "SELECT customerId FROM vip UNION SELECT customerId FROM overdue",
    )
)

# 场景8: 非法表名被拦截
results.append(
    run_case(
        "8. 非法表名拦截（Supplier 未定义）",
        "SELECT * FROM Supplier WHERE id = 1",
        expect_error="INVALID_TABLE",
    )
)

# 场景9: 非法列名被拦截
results.append(
    run_case(
        "9. 非法列名拦截（Order 没有 color 属性）",
        "SELECT orderNo, color FROM Order WHERE status = 'PAID'",
        expect_error="INVALID_COLUMN",
    )
)

# 场景10: 非法 JOIN 被拦截（Product ↔ Customer 未定义 LinkType）
results.append(
    run_case(
        "10. 非法JOIN拦截（Product↔Customer 无 LinkType）",
        "SELECT p.productName, c.name FROM Product p JOIN Customer c ON p.productId = c.customerId",
        expect_error="INVALID_JOIN",
    )
)

# 场景11: 三个表 JOIN（Order 居中，连接 Customer + Product）
results.append(
    run_case(
        "11. 三表JOIN（Order-Customer + Order-Product）",
        "SELECT o.orderNo, c.name, p.productName FROM Order o "
        "JOIN Customer c ON o.customerId = c.customerId "
        "JOIN Product p ON o.productId = p.productId "
        "WHERE o.status = 'PAID'",
    )
)

# 场景12: SQL 注入尝试（字面量里塞单引号）
results.append(
    run_case(
        "12. SQL注入尝试（字面量参数化）",
        "SELECT orderNo FROM Order WHERE status = 'PAID' OR 1=1; DROP TABLE x; --'",
    )
)

print(f"\n{'=' * 70}\n结果: {sum(results)}/{len(results)} 通过")
for i, r in enumerate(results, 1):
    print(f"  场景{i}: {'✓' if r else '✗'}")
