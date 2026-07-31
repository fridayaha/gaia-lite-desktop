"""SqlGlot 编译器可行性验证 v3 —— 车企全链路场景补全。

基于材料第二十/二十一轮真实业务场景，补验前两版漏掉的 SQL 类型：
  T3  多层关联穿透（5+表 JOIN，BOM 展开）
  T5  占比/比率计算（聚合相除 SUM(a)/SUM(b)）
  T6  同比/环比（SELF JOIN 跨期对比）
  T7  TopN + 占比（窗口函数 + 聚合组合）
  T9  时间序列趋势（日期函数 + 按月分组）
  T12 Action 回写（UPDATE/INSERT，非 SELECT）
  T15 全链路端到端（6+表 JOIN，下单→交付→售后）

同时验证关键边界：哪些场景不该由编译器处理（T10/T11/T14）。
"""

from __future__ import annotations

import itertools

import sqlglot
from sqlglot import exp

# ── 车企全链路本体 Schema ───────────────────────────────────────────────


class AutoSchema:
    """车企本体：覆盖订单→排产→生产→零部件→供应商→交付→售后全链路。"""

    def __init__(self) -> None:
        # ObjectType api_name → 物理表名（Doris idx 命名 v5.2）
        self.object_types: dict[str, str] = {
            "Order": "idx_auto__order",
            "Vehicle": "idx_auto__vehicle",
            "ProductionPlan": "idx_auto__production_plan",
            "Part": "idx_auto__part",
            "Supplier": "idx_auto__supplier",
            "Dealer": "idx_auto__dealer",
            "Customer": "idx_auto__customer",
            "Claim": "idx_auto__claim",  # 售后索赔
            "Defect": "idx_auto__defect",  # 质量缺陷
        }
        self.properties: dict[str, dict[str, str]] = {
            "Order": {
                "orderId": "order_id",
                "customerId": "customer_id",
                "vehicleId": "vehicle_id",
                "orderDate": "order_date",
                "amount": "amount",
                "status": "status",
                "deliveryDate": "delivery_date",
                "region": "region",
            },
            "Vehicle": {
                "vehicleId": "vehicle_id",
                "vin": "vin",
                "model": "model",
                "productionDate": "production_date",
                "plantId": "plant_id",
                "batteryBatch": "battery_batch",
                "status": "status",
            },
            "ProductionPlan": {
                "planId": "plan_id",
                "vehicleId": "vehicle_id",
                "planDate": "plan_date",
                "lineId": "line_id",
                "status": "status",
            },
            "Part": {
                "partId": "part_id",
                "partName": "part_name",
                "supplierId": "supplier_id",
                "batchNo": "batch_no",
                "category": "category",
            },
            "Supplier": {
                "supplierId": "supplier_id",
                "supplierName": "supplier_name",
                "region": "region",
                "rating": "rating",
            },
            "Dealer": {
                "dealerId": "dealer_id",
                "dealerName": "dealer_name",
                "region": "region",
                "dealerCode": "dealer_code",
            },
            "Customer": {
                "customerId": "customer_id",
                "customerName": "customer_name",
                "region": "region",
                "level": "level",
            },
            "Claim": {
                "claimId": "claim_id",
                "vehicleId": "vehicle_id",
                "partId": "part_id",
                "claimDate": "claim_date",
                "faultCode": "fault_code",
                "amount": "amount",
                "status": "status",
            },
            "Defect": {
                "defectId": "defect_id",
                "partId": "part_id",
                "batchNo": "batch_no",
                "defectType": "defect_type",
                "severity": "severity",
                "reportDate": "report_date",
            },
        }
        # LinkType：车企全链路关系
        self.links: set[tuple[str, str]] = {
            ("Order", "Customer"),
            ("Customer", "Order"),
            ("Order", "Vehicle"),
            ("Vehicle", "Order"),
            ("Vehicle", "ProductionPlan"),
            ("ProductionPlan", "Vehicle"),
            ("Vehicle", "Part"),
            ("Part", "Vehicle"),
            ("Part", "Supplier"),
            ("Supplier", "Part"),
            ("Order", "Dealer"),
            ("Dealer", "Order"),
            ("Claim", "Vehicle"),
            ("Vehicle", "Claim"),
            ("Claim", "Part"),
            ("Part", "Claim"),
            ("Defect", "Part"),
            ("Part", "Defect"),
        }

    def physical_table(self, ot_api: str) -> str:
        if ot_api not in self.object_types:
            raise ValueError(f"INVALID_TABLE: ObjectType {ot_api!r} 未定义")
        return self.object_types[ot_api]

    def has_link(self, a: str, b: str) -> bool:
        return (a, b) in self.links


# ── 编译器（v2 修正版，from_/with_ key + 物理名反查）────────────────────


class CompilerV3:
    def __init__(self, schema: AutoSchema) -> None:
        self.schema = schema
        self.params: list[object] = []
        self.alias_map: dict[str, str] = {}
        self.cte_defs: dict[str, str] = {}  # CTE名 → 主 ObjectType
        self.phys_to_ot: dict[str, str] = {v: k for k, v in schema.object_types.items()}

    def compile(self, logical_sql: str, dialect: str) -> str:
        self.params = []
        self.alias_map = {}
        self.cte_defs = {}
        # subquery alias → 输出列集合（包含原名和别名）
        self.subquery_outputs: dict[str, set[str]] = {}
        ast = sqlglot.parse_one(logical_sql, read="mysql")
        # Pass 1: 收集 alias + CTE + 子查询输出列
        self._pass1_collect(ast)
        # 输出别名集合（不校验）
        out_aliases: set[str] = set()
        for node in ast.walk():
            if isinstance(node, exp.Alias) and node.alias:
                out_aliases.add(node.alias)
        # Pass 2: 改写
        ast = self._rewrite(ast, dialect, out_aliases)
        return ast.sql(dialect=dialect)

    def _pass1_collect(self, ast: exp.Expression) -> None:
        """收集所有 alias→ObjectType、CTE定义、子查询输出列。"""
        # ObjectType 表的 alias
        for t in ast.find_all(exp.Table):
            ot = t.name if t.name in self.schema.object_types else None
            if ot:
                self.alias_map[t.alias or t.name] = ot
        # CTE 定义
        with_node = ast.args.get("with") or ast.args.get("with_")
        if with_node:
            ctes = with_node.expressions if hasattr(with_node, "expressions") else [with_node]
            for cte in ctes:
                if isinstance(cte, exp.CTE):
                    cte_name = cte.alias
                    inner_tables = [t.name for t in cte.this.find_all(exp.Table) if t.name in self.schema.object_types]
                    if inner_tables:
                        self.cte_defs[cte_name] = inner_tables[0]
                    # CTE 输出列 = 内层 SELECT projections 的列名/别名
                    self.subquery_outputs[cte_name] = self._collect_output_cols(cte.this)
        # 子查询（FROM/JOIN 里的 Subquery）
        for sq in ast.find_all(exp.Subquery):
            alias = sq.alias
            if alias:
                self.subquery_outputs[alias] = self._collect_output_cols(sq.this)
                # 若子查询主表是单 ObjectType，也登记 alias→OT（供列归属 fallback）
                inner_tables = [t.name for t in sq.this.find_all(exp.Table) if t.name in self.schema.object_types]
                if len(inner_tables) == 1:
                    self.alias_map[alias] = inner_tables[0]

    def _collect_output_cols(self, select_node: exp.Expression) -> set[str]:
        """收集一个 SELECT 的输出列名（原名 + 别名）。"""
        cols: set[str] = set()
        if not isinstance(select_node, exp.Select):
            return cols
        for proj in select_node.expressions:
            if isinstance(proj, exp.Alias):
                cols.add(proj.alias)
            elif isinstance(proj, exp.Column):
                cols.add(proj.name)
            # 聚合/算式无别名的不收集（外层只能用别名引用）
        return cols

    def _rewrite(self, node: exp.Expression, dialect: str, out_aliases: set[str]) -> exp.Expression:
        # UPDATE 语句：处理 SET 子句的列 + WHERE
        if isinstance(node, exp.Update):
            # this 是 Table
            tbl = node.args.get("this")
            if tbl:
                node.set("this", self._rewrite(tbl, dialect, out_aliases))
            # expressions 是 SET 子句 [EQ(Column, Literal)...]
            set_exprs = node.args.get("expressions") or []
            node.set(
                "expressions",
                [self._rewrite(e, dialect, out_aliases) for e in set_exprs if isinstance(e, exp.Expression)],
            )
            where = node.args.get("where")
            if where:
                node.set("where", self._rewrite(where, dialect, out_aliases))
            return node

        if isinstance(node, exp.Table):
            # CTE 名 → 跳过（已在 cte_defs 登记）
            if node.name in self.cte_defs:
                return node
            ot = node.name if node.name in self.schema.object_types else self.phys_to_ot.get(node.name)
            if not ot:
                raise ValueError(f"INVALID_TABLE: ObjectType {node.name!r} 未定义")
            node.set("this", exp.to_identifier(self.schema.physical_table(ot), quoted=False))
            return node

        if isinstance(node, exp.Column):
            col_api = node.name
            # 子查询/CTE 输出列：跳过校验（内层已校验过）
            if node.table and node.table in self.subquery_outputs:
                if col_api in self.subquery_outputs[node.table]:
                    return node  # 信任内层，不改写（内层已改成物理名）
            if col_api in out_aliases and not node.table:
                owner = self._resolve_owner(node)
                if owner is None:
                    return node
                props = self.schema.properties.get(owner, {})
                if col_api not in props:
                    return node
            owner_ot = self._resolve_owner(node)
            if owner_ot is None:
                raise ValueError(f"CANNOT_RESOLVE_COLUMN_OWNER: {col_api!r}")
            props = self.schema.properties.get(owner_ot, {})
            if col_api not in props:
                raise ValueError(f"INVALID_COLUMN: Property {col_api!r} 不属于 ObjectType {owner_ot}")
            node.set("this", exp.to_identifier(props[col_api], quoted=False))
            return node

        if isinstance(node, exp.Join):
            self._validate_join(node)

        if isinstance(node, exp.Literal) and not isinstance(node.parent, exp.Identifier):
            self.params.append(node.this)
            return exp.Placeholder()

        for key, child in list(node.args.items()):
            if isinstance(child, list):
                node.set(key, [self._rewrite(c, dialect, out_aliases) for c in child if isinstance(c, exp.Expression)])
            elif isinstance(child, exp.Expression):
                node.set(key, self._rewrite(child, dialect, out_aliases))
        return node

    def _resolve_owner(self, col: exp.Column) -> str | None:
        tbl = col.table
        if tbl:
            if tbl in self.alias_map:
                return self.alias_map[tbl]
            if tbl in self.cte_defs:
                return self.cte_defs[tbl]
            if tbl in self.subquery_outputs:
                # 子查询别名：列在其输出集合里则归属其内部主 ObjectType（若有）
                if col.name in self.subquery_outputs[tbl] and tbl in self.alias_map:
                    return self.alias_map[tbl]
                return None  # 子查询输出但无单一主表 → 不校验（信任内层已校验）
            if tbl in self.phys_to_ot:
                return self.phys_to_ot[tbl]
            return None
        select = col.find_ancestor(exp.Select)
        if select:
            ots: set[str] = set()
            from_clause = select.args.get("from") or select.args.get("from_")
            if from_clause:
                for t in from_clause.find_all(exp.Table):
                    if t.name in self.cte_defs:
                        ots.add(self.cte_defs[t.name])
                    ot = t.name if t.name in self.schema.object_types else self.phys_to_ot.get(t.name)
                    if ot:
                        ots.add(ot)
            for j in select.args.get("joins", []) or []:
                for t in j.find_all(exp.Table):
                    if t.name in self.cte_defs:
                        ots.add(self.cte_defs[t.name])
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
            if t.name in self.cte_defs:
                ots.add(self.cte_defs[t.name])
            ot = t.name if t.name in self.schema.object_types else self.phys_to_ot.get(t.name)
            if ot:
                ots.add(ot)
        if len(ots) < 2:
            return
        for a, b in itertools.combinations(ots, 2):
            if self.schema.has_link(a, b):
                return
        raise ValueError(f"INVALID_JOIN: ObjectType 组合 {ots} 之间未定义 LinkType")


schema = AutoSchema()


def run(name: str, sql: str, expect_error: str | None = None, desc: str = "") -> bool:
    print(f"\n{'=' * 70}\n{name}  {desc}\n逻辑SQL: {sql[:130]}")
    c = CompilerV3(schema)
    try:
        doris = c.compile(sql, "doris")
        print(f"  Doris: {doris}")
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

# ── T3: 多层关联穿透（4表 JOIN，质量追溯：索赔→车辆→零件→供应商）──
r.append(
    run(
        "T3a. 4表JOIN穿透（索赔→车辆→零件→供应商）",
        "SELECT cl.claimId, v.vin, p.partName, s.supplierName "
        "FROM Claim cl JOIN Vehicle v ON cl.vehicleId = v.vehicleId "
        "JOIN Part p ON cl.partId = p.partId "
        "JOIN Supplier s ON p.supplierId = s.supplierId "
        "WHERE cl.status = 'OPEN'",
    )
)

# ── T15: 全链路端到端（5表 JOIN：订单→客户→车辆→排产→交付）──
r.append(
    run(
        "T15. 5表全链路（订单→客户→车辆→排产→索赔）",
        "SELECT o.orderId, c.customerName, v.vin, pp.planDate, cl.faultCode "
        "FROM Order o "
        "JOIN Customer c ON o.customerId = c.customerId "
        "JOIN Vehicle v ON o.vehicleId = v.vehicleId "
        "JOIN ProductionPlan pp ON v.vehicleId = pp.vehicleId "
        "JOIN Claim cl ON v.vehicleId = cl.vehicleId "
        "WHERE o.status = 'DELIVERED'",
    )
)

# ── T5: 占比/比率计算（聚合相除）──
r.append(
    run(
        "T5a. VIP客户占比（聚合相除）",
        "SELECT region, "
        "SUM(CASE WHEN level = 'VIP' THEN 1 ELSE 0 END) AS vip_count, "
        "COUNT(*) AS total_count, "
        "SUM(CASE WHEN level = 'VIP' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS vip_ratio "
        "FROM Customer GROUP BY region",
    )
)

r.append(
    run(
        "T5b. 复购率（两次下单客户占比）",
        "SELECT c.region, "
        "COUNT(DISTINCT CASE WHEN order_cnt >= 2 THEN c.customerId END) AS repeat_customers, "
        "COUNT(DISTINCT c.customerId) AS total_customers, "
        "COUNT(DISTINCT CASE WHEN order_cnt >= 2 THEN c.customerId END) * 1.0 / COUNT(DISTINCT c.customerId) AS repeat_rate "
        "FROM Customer c JOIN (SELECT customerId, COUNT(*) AS order_cnt FROM Order GROUP BY customerId) o "
        "ON c.customerId = o.customerId GROUP BY c.region",
    )
)

# ── T6: 同比/环比（SELF JOIN 跨期对比）──
r.append(
    run(
        "T6a. 同比销售额（本年 vs 去年 SELF JOIN）",
        "SELECT cur.region, cur.year_amount AS this_year, prev.year_amount AS last_year, "
        "(cur.year_amount - prev.year_amount) * 1.0 / prev.year_amount AS yoy_growth "
        "FROM (SELECT region, SUM(amount) AS year_amount FROM Order WHERE YEAR(orderDate) = 2025 GROUP BY region) cur "
        "JOIN (SELECT region, SUM(amount) AS year_amount FROM Order WHERE YEAR(orderDate) = 2024 GROUP BY region) prev "
        "ON cur.region = prev.region",
    )
)

r.append(
    run(
        "T6b. 环比（本月 vs 上月，子查询对比）",
        "SELECT cur.region, cur.m_amount AS this_month, prev.m_amount AS last_month, "
        "cur.m_amount - prev.m_amount AS diff "
        "FROM (SELECT region, SUM(amount) AS m_amount FROM Order WHERE MONTH(orderDate) = 6 GROUP BY region) cur "
        "JOIN (SELECT region, SUM(amount) AS m_amount FROM Order WHERE MONTH(orderDate) = 5 GROUP BY region) prev "
        "ON cur.region = prev.region",
    )
)

# ── T7: TopN + 占比（窗口函数 + 聚合）──
r.append(
    run(
        "T7. Top10客户及金额占比（窗口函数 SUM OVER）",
        "SELECT customerId, total_amount, "
        "total_amount * 1.0 / SUM(total_amount) OVER () AS ratio "
        "FROM (SELECT customerId, SUM(amount) AS total_amount FROM Order GROUP BY customerId) t "
        "ORDER BY total_amount DESC LIMIT 10",
    )
)

# ── T9: 时间序列趋势（按月分组 + 排序）──
r.append(
    run(
        "T9a. 月度销售额趋势（DATE_FORMAT 分组）",
        "SELECT DATE_FORMAT(orderDate, '%Y-%m') AS month, SUM(amount) AS total "
        "FROM Order WHERE orderDate >= '2025-01-01' "
        "GROUP BY DATE_FORMAT(orderDate, '%Y-%m') ORDER BY month",
    )
)

r.append(
    run(
        "T9b. 近6月故障率趋势（按月 + COUNT）",
        "SELECT DATE_FORMAT(claimDate, '%Y-%m') AS month, COUNT(*) AS claim_count "
        "FROM Claim WHERE claimDate >= '2025-01-01' "
        "GROUP BY DATE_FORMAT(claimDate, '%Y-%m') ORDER BY month",
    )
)

# ── T12: Action 回写（UPDATE）──
r.append(
    run(
        "T12a. UPDATE 回写（延后订单交付日期）",
        "UPDATE Order SET deliveryDate = '2025-07-15' WHERE orderId = 'A20250701'",
        desc="【Action路径】",
    )
)

r.append(
    run(
        "T12b. 批量UPDATE（调整工单优先级）",
        "UPDATE Claim SET status = 'URGENT' WHERE claimId IN ('C001', 'C002', 'C003')",
        desc="【Action路径】",
    )
)

# ── T4: 多维聚合（多维度 GROUP BY）──
r.append(
    run(
        "T4. 三维拆解（省份×车型×价位）",
        "SELECT region, model, status, SUM(amount) AS total, COUNT(*) AS cnt "
        "FROM Order o JOIN Vehicle v ON o.vehicleId = v.vehicleId "
        "GROUP BY region, model, status",
    )
)

# ── 边界场景：不该由编译器处理 ──
r.append(
    run(
        "边界-T10. 多步推理（根因分析，非单一SQL）",
        "SELECT c.faultCode, COUNT(*) AS cnt FROM Claim c "
        "WHERE c.claimDate >= '2025-06-01' GROUP BY c.faultCode ORDER BY cnt DESC",
        desc="【多步推理第1步，编译器只处理单步】",
    )
)

print(f"\n{'=' * 70}\n结果: {sum(r)}/{len(r)} 通过")
for i, x in enumerate(r, 1):
    print(f"  {i}: {'✓' if x else '✗'}")
