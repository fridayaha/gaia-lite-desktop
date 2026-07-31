/**
 * 连接器目录元数据 — 多源异构数据融合（multi-source-data-fusion-design.md §五）
 *
 * 对标 Palantir Foundry Data Connection 的连接器卡片 + 能力标签模型。
 * 这是一个数据结构（配置驱动），不是插件体系（G4 不过度抽象）。
 *
 * 能力标签语义（Capability）：
 *   explore        探索 schema
 *   batch_sync     批量同步
 *   cdc            增量 CDC
 *   virtual_table  VIRTUAL 联邦不落地
 *   streaming_sync 流式同步
 *   file_sync      文件同步
 *
 * 品类一刀切策略（§8.1）：NoSQL/时序/文件存储/达梦/MaxCompute 一律 MANAGED 落地，
 * 不开 virtual_table 能力（决策点 4 严格一刀切）。
 */

/**
 * 连接器品类（对齐 Palantir Foundry Data Connection 顶层分组）：
 *   - Filesystems and blob stores → file_storage
 *   - JDBC sources (含 data warehouses) → databases
 *   - Streaming sources → streaming
 *   - NoSQL stores → nosql
 *   - Lakehouse formats → lakehouse
 *   - Other / Generic → generic
 *
 * 对比旧版变更：relational + cloud_warehouse 合并为 databases；
 * file_object → file_storage；messaging → streaming。
 */
export type ConnectorCategory =
  | 'databases' // 数据库（关系型 + 云数仓，含国产库）
  | 'lakehouse' // 湖仓格式（Iceberg/Hive/Delta/Hudi/Paimon）
  | 'file_storage' // 文件与对象存储（S3/MinIO/OSS/HDFS）
  | 'streaming' // 消息与流式（Kafka）
  | 'nosql' // NoSQL（Elasticsearch 等）
  | 'generic'; // 通用 / 其他

export type Capability =
  | 'explore'
  | 'batch_sync'
  | 'cdc'
  | 'virtual_table'
  | 'streaming_sync'
  | 'file_sync';

export type Maturity = 'GA' | 'Beta' | 'Alpha';

/**
 * 厂商标记 — 用于目录排序（华为优先于业界）。
 * huawei = 华为系（OpenGauss / GaussDB / GaussDB DWS）
 */
export type Vendor = 'huawei' | 'other';

/** 配置字段定义（驱动 Step 2 动态表单渲染） */
export interface ConfigField {
  key: string;
  label: string;
  /** input | password | select */
  type?: 'input' | 'password' | 'select';
  placeholder?: string;
  required?: boolean;
  hint?: string;
  /** select 类型的可选值 */
  options?: string[];
  /** 该字段的默认值 */
  default?: string;
  /** 占位宽度比例（flex 值），用于 form-row 布局 */
  flex?: number;
}

export interface ConnectorMeta {
  /** connector_type，与后端 CAPABILITY_MAP key 对齐 */
  key: string;
  /** 显示图标（emoji，避免引入图标库依赖；G4 轻量替代） */
  icon: string;
  /** 显示名 */
  label: string;
  /** 一句话简介 */
  description: string;
  category: ConnectorCategory;
  maturity: Maturity;
  /** 能力标签（与后端 CAPABILITY_MAP 对齐） */
  capabilities: Capability[];
  /** 默认端口（JDBC 类，空串表示不适用） */
  defaultPort: string;
  /** 配置表单元数据（驱动 Step 2 动态渲染） */
  configSchema: ConfigField[];
  /** 避坑提示（连接器详情面板展示） */
  pitfalls?: string[];
  /** 是否 PG 内核（影响 explore 默认 schema 行为） */
  pgKernel?: boolean;
  /** 是否 MySQL 协议（影响 explore 默认 database 行为） */
  mysqlProto?: boolean;
  /** 搜索关键词（口语化别名，供目录页搜索匹配，如「对象存储」「国产库」） */
  keywords?: string[];
  /** 厂商（华为系优先排序，其余按业界维度排） */
  vendor?: Vendor;
  /** 业界流行度（1-10，越高越主流；用于目录排序第二优先级） */
  popularity?: number;
  /** 业界使用量（1-10，越高装机量越大；用于目录排序第三优先级） */
  usage?: number;
}

// ── 通用配置字段片段 ──

const JDBC_HOST_FIELD: ConfigField = {
  key: 'host',
  label: '主机',
  placeholder: 'db.internal.com',
  required: true,
  flex: 2,
};

const jdbcPortField = (port: string): ConfigField => ({
  key: 'port',
  label: '端口',
  placeholder: port,
  required: true,
  flex: 1,
});

const JDBC_DATABASE_FIELD: ConfigField = {
  key: 'database',
  label: '数据库名',
  placeholder: 'erp_prod',
  required: true,
};

const JDBC_USER_FIELD: ConfigField = {
  key: 'username',
  label: '用户名',
  placeholder: 'gaia_sync',
  flex: 1,
};

const JDBC_PASSWORD_FIELD: ConfigField = {
  key: 'password',
  label: '密码',
  type: 'password',
  placeholder: '••••••••',
  flex: 1,
};

// ── 连接器目录 ──

export const CONNECTOR_CATALOG: ConnectorMeta[] = [
  // ── 数据库（关系型 + 云数仓，含国产库）──
  {
    key: 'mysql',
    icon: '🐬',
    label: 'MySQL',
    description: '开源关系型数据库，支持 Binlog CDC 实时同步',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'cdc', 'virtual_table'],
    defaultPort: '3306',
    mysqlProto: true,
    keywords: ['关系型', '数据库', '开源', 'binlog'],
    popularity: 10,
    usage: 10,
    configSchema: [JDBC_HOST_FIELD, jdbcPortField('3306'), JDBC_DATABASE_FIELD, JDBC_USER_FIELD, JDBC_PASSWORD_FIELD],
  },
  {
    key: 'postgresql',
    icon: '🐘',
    label: 'PostgreSQL',
    description: '开源关系型数据库，支持 WAL CDC 实时同步',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'cdc', 'virtual_table'],
    defaultPort: '5432',
    pgKernel: true,
    keywords: ['关系型', '数据库', '开源', 'pg', 'postgres'],
    popularity: 10,
    usage: 9,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('5432'),
      JDBC_DATABASE_FIELD,
      { key: 'schema', label: 'Schema', placeholder: 'public', hint: 'PG 库内逻辑分组，默认 public', flex: 1 },
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
  },
  {
    key: 'opengauss',
    icon: '🔵',
    label: 'OpenGauss',
    description: '华为开源国产数据库，PG 兼容，支持原生 CDC',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'cdc', 'virtual_table'],
    defaultPort: '5432',
    pgKernel: true,
    keywords: ['国产库', '国产', '华为', '关系型', '数据库'],
    vendor: 'huawei',
    popularity: 7,
    usage: 6,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('5432'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: [
      '必须使用独立类名驱动 com.huawei.opengauss.jdbc.Driver，避免与官方 PG 驱动同名类冲突',
      'URL scheme 为 jdbc:opengauss://（非 postgresql://）',
    ],
  },
  {
    key: 'gaussdb',
    icon: '🟢',
    label: 'GaussDB',
    description: '华为分布式数据库，PG 兼容',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'cdc', 'virtual_table'],
    defaultPort: '25308',
    pgKernel: true,
    keywords: ['国产库', '国产', '华为', '关系型', '数据库'],
    vendor: 'huawei',
    popularity: 7,
    usage: 5,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('25308'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['必须使用 gsjdbc200.jar（com.huawei.gauss200.jdbc.Driver），gsjdbc4.jar 与 PG 驱动同名冲突'],
  },
  {
    key: 'tidb',
    icon: '🟡',
    label: 'TiDB',
    description: 'PingCAP 国产分布式数据库，MySQL 兼容，支持原生 CDC',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'cdc', 'virtual_table'],
    defaultPort: '4000',
    mysqlProto: true,
    keywords: ['国产库', '国产', 'PingCAP', '分布式', '关系型'],
    popularity: 8,
    usage: 7,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('4000'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['CDC 需配置 PD 地址（pd-addresses）'],
  },
  {
    key: 'oceanbase',
    icon: '🌊',
    label: 'OceanBase',
    description: '蚂蚁国产分布式数据库，MySQL 模式',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'virtual_table'],
    defaultPort: '2883',
    mysqlProto: true,
    keywords: ['国产库', '国产', '蚂蚁', '分布式', '关系型'],
    popularity: 8,
    usage: 6,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('2883'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['仅支持 MySQL 模式；CDC 走 OMS，非 SeaTunnel 原生'],
  },
  {
    key: 'starrocks',
    icon: '⭐',
    label: 'StarRocks',
    description: '国产 MPP 数据库，MySQL 协议，支持 VIRTUAL 联邦与落地',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'virtual_table'],
    defaultPort: '9030',
    mysqlProto: true,
    keywords: ['国产库', '国产', 'MPP', '分析型', '关系型'],
    popularity: 8,
    usage: 6,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('9030'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['FE 9030 兼容 MySQL 协议；可走 Gravitino jdbc-starrocks 联邦或 SeaTunnel 专用 connector 落地'],
  },
  {
    key: 'dameng',
    icon: '🟥',
    label: '达梦 DM',
    description: '达梦国产数据库，独立 dialect',
    category: 'databases',
    maturity: 'Beta',
    capabilities: ['explore', 'batch_sync'],
    defaultPort: '5236',
    keywords: ['国产库', '国产', '达梦', 'dm', '关系型'],
    popularity: 5,
    usage: 5,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('5236'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['无 Gravitino provider，仅支持 MANAGED 落地（无 VIRTUAL 联邦）', '无原生 CDC'],
  },
  {
    key: 'kingbase',
    icon: '👑',
    label: '人大金仓',
    description: '人大金仓国产数据库，PG 兼容',
    category: 'databases',
    maturity: 'Beta',
    capabilities: ['explore', 'batch_sync', 'virtual_table'],
    defaultPort: '54321',
    pgKernel: true,
    keywords: ['国产库', '国产', '金仓', '关系型'],
    popularity: 5,
    usage: 5,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('54321'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['CDC 走 Kafka CDC，非 SeaTunnel 原生'],
  },
  {
    key: 'generic_jdbc',
    icon: '🔌',
    label: '通用 JDBC',
    description: '任意 JDBC 兼容库兜底，无 Gravitino catalog，仅落地',
    category: 'generic',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync'],
    defaultPort: '',
    keywords: ['jdbc', '兜底', '通用', '自定义'],
    popularity: 3,
    usage: 3,
    configSchema: [
      {
        key: 'url',
        label: 'JDBC URL',
        placeholder: 'jdbc:custom://host:1234/db',
        required: true,
        flex: 2,
      },
      { key: 'driver', label: 'Driver 类名', placeholder: 'com.example.Driver', required: true, flex: 1 },
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['无 Gravitino catalog，仅支持 MANAGED 落地', '需手动提供完整 JDBC URL 与 Driver 类名'],
  },

  // ── 湖仓格式 ──
  {
    key: 'iceberg',
    icon: '🧊',
    label: 'Iceberg',
    description: 'Gaia 主存储格式',
    category: 'lakehouse',
    maturity: 'GA',
    capabilities: ['explore', 'virtual_table'],
    defaultPort: '',
    keywords: ['湖仓', '数据湖', '表格式'],
    popularity: 9,
    usage: 8,
    configSchema: [{ key: 'warehouse', label: 'Warehouse', placeholder: 's3://warehouse' }],
  },
  {
    key: 'hive',
    icon: '🐝',
    label: 'Hive',
    description: '联邦源，查询已有 Hive 表不搬迁',
    category: 'lakehouse',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'virtual_table'],
    defaultPort: '',
    keywords: ['湖仓', '数据湖', 'metastore'],
    popularity: 8,
    usage: 8,
    configSchema: [
      { key: 'metastore-uri', label: 'Metastore URI', placeholder: 'thrift://hms:9083', required: true },
    ],
    pitfalls: ['需 Hive Metastore 网络可达'],
  },
  {
    key: 'delta',
    icon: '🔺',
    label: 'Delta Lake',
    description: '联邦源，Gravitino Generic Lakehouse 纳管',
    category: 'lakehouse',
    maturity: 'GA',
    capabilities: ['explore', 'virtual_table'],
    defaultPort: '',
    keywords: ['湖仓', '数据湖', '表格式'],
    popularity: 7,
    usage: 6,
    configSchema: [
      { key: 'catalog-backend', label: 'Catalog Backend', placeholder: 'hive', required: true },
      { key: 'warehouse', label: 'Warehouse', placeholder: 's3://delta/wh', required: true },
    ],
  },
  {
    key: 'hudi',
    icon: '🪶',
    label: 'Hudi',
    description: '联邦源，Gravitino Generic Lakehouse 纳管',
    category: 'lakehouse',
    maturity: 'GA',
    capabilities: ['explore', 'virtual_table'],
    defaultPort: '',
    keywords: ['湖仓', '数据湖', '表格式'],
    popularity: 6,
    usage: 5,
    configSchema: [{ key: 'warehouse', label: 'Warehouse', placeholder: 's3://hudi/wh', required: true }],
  },
  {
    key: 'paimon',
    icon: '🪵',
    label: 'Paimon',
    description: '联邦源，Gravitino Generic Lakehouse 纳管',
    category: 'lakehouse',
    maturity: 'GA',
    capabilities: ['explore', 'virtual_table'],
    defaultPort: '',
    keywords: ['湖仓', '数据湖', '表格式'],
    popularity: 5,
    usage: 4,
    configSchema: [{ key: 'warehouse', label: 'Warehouse', placeholder: 's3://paimon/wh', required: true }],
  },

  // ── 文件与对象存储 ──
  {
    key: 's3',
    icon: '🪣',
    label: 'Amazon S3',
    description: '对象存储，支持 Parquet/CSV/JSON 文件落地',
    category: 'file_storage',
    maturity: 'GA',
    capabilities: ['explore', 'file_sync'],
    defaultPort: '',
    keywords: ['对象存储', '文件', 'aws', '云存储'],
    popularity: 10,
    usage: 9,
    configSchema: [
      { key: 'endpoint', label: 'Endpoint', placeholder: 'http://s3.local:9000', required: true },
      { key: 'access_key', label: 'Access Key', flex: 1 },
      { key: 'secret_key', label: 'Secret Key', type: 'password', flex: 1 },
      { key: 'bucket', label: 'Bucket', placeholder: 'my-bucket' },
    ],
    pitfalls: ['只能 MANAGED 落地，无 VIRTUAL 联邦（裸文件不能 SQL 查询）'],
  },
  {
    key: 'minio',
    icon: '🟠',
    label: 'MinIO',
    description: 'S3 兼容对象存储，走 S3 协议',
    category: 'file_storage',
    maturity: 'GA',
    capabilities: ['explore', 'file_sync'],
    defaultPort: '',
    keywords: ['对象存储', '文件', 's3兼容'],
    popularity: 9,
    usage: 8,
    configSchema: [
      { key: 'endpoint', label: 'Endpoint', placeholder: 'http://minio:9000', required: true },
      { key: 'access_key', label: 'Access Key', flex: 1 },
      { key: 'secret_key', label: 'Secret Key', type: 'password', flex: 1 },
      { key: 'bucket', label: 'Bucket', placeholder: 'my-bucket' },
    ],
    pitfalls: ['必须走 S3File + endpoint，不能用 OssFile（不支持 MinIO）'],
  },
  {
    key: 'oss',
    icon: '☁️',
    label: '阿里云 OSS',
    description: '对象存储，S3 兼容协议',
    category: 'file_storage',
    maturity: 'GA',
    capabilities: ['explore', 'file_sync'],
    defaultPort: '',
    keywords: ['对象存储', '文件', '阿里云', '云存储'],
    popularity: 8,
    usage: 7,
    configSchema: [
      { key: 'endpoint', label: 'Endpoint', placeholder: 'http://oss-cn-hangzhou.aliyuncs.com', required: true },
      { key: 'access_key', label: 'Access Key', flex: 1 },
      { key: 'secret_key', label: 'Secret Key', type: 'password', flex: 1 },
      { key: 'bucket', label: 'Bucket', placeholder: 'my-bucket' },
    ],
  },
  {
    key: 'hdfs',
    icon: '📁',
    label: 'HDFS',
    description: 'Hadoop 分布式文件系统',
    category: 'file_storage',
    maturity: 'GA',
    capabilities: ['explore', 'file_sync'],
    defaultPort: '',
    keywords: ['文件', 'hadoop', '分布式文件系统'],
    popularity: 7,
    usage: 7,
    configSchema: [
      { key: 'endpoint', label: 'NameNode URI', placeholder: 'hdfs://namenode:8020', required: true },
      { key: 'path', label: '路径', placeholder: '/data/warehouse' },
    ],
  },

  // ── 消息队列 ──
  {
    key: 'kafka',
    icon: '📨',
    label: 'Kafka',
    description: '消息队列，VIRTUAL 联邦 + 流式落地双通道',
    category: 'streaming',
    maturity: 'GA',
    capabilities: ['explore', 'streaming_sync', 'virtual_table'],
    defaultPort: '9092',
    keywords: ['消息队列', '流式', '流处理', 'mq'],
    popularity: 10,
    usage: 9,
    configSchema: [
      {
        key: 'bootstrap_servers',
        label: 'Bootstrap Servers',
        placeholder: 'kafka:9092',
        required: true,
      },
    ],
    pitfalls: ['消费组需与内部 Action CDC 隔离'],
  },

  // ── NoSQL ──
  {
    key: 'elasticsearch',
    icon: '🔍',
    label: 'Elasticsearch',
    description: '搜索型 NoSQL，落地为主（严格一刀切）',
    category: 'nosql',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync'],
    defaultPort: '9200',
    keywords: ['nosql', '搜索', 'es', '全文检索'],
    popularity: 9,
    usage: 8,
    configSchema: [
      { key: 'hosts', label: 'Hosts', placeholder: 'es:9200', required: true },
      { key: 'username', label: '用户名', flex: 1 },
      { key: 'password', label: '密码', type: 'password', flex: 1 },
      { key: 'index', label: 'Index', placeholder: 'logs-*' },
    ],
    pitfalls: ['严格一刀切：一律落地，不开 Trino 联邦口子（决策点 4）', 'text 字段映射为 Iceberg string'],
  },

  // ── 云数仓（并入 databases 大类）──
  {
    key: 'analyticdb_pg',
    icon: '📊',
    label: 'AnalyticDB PG',
    description: '阿里云分析型数据库，PG 内核，复用 PG 通道',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'virtual_table'],
    defaultPort: '5432',
    pgKernel: true,
    keywords: ['云数仓', '阿里云', '分析型', '国产'],
    popularity: 6,
    usage: 5,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('5432'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
  },
  {
    key: 'gaussdb_dws',
    icon: '📈',
    label: 'GaussDB DWS',
    description: '华为云数据仓库服务，PG 内核，复用 PG 通道',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync', 'virtual_table'],
    defaultPort: '8000',
    pgKernel: true,
    keywords: ['云数仓', '华为', '分析型', '国产'],
    vendor: 'huawei',
    popularity: 6,
    usage: 5,
    configSchema: [
      JDBC_HOST_FIELD,
      jdbcPortField('8000'),
      JDBC_DATABASE_FIELD,
      JDBC_USER_FIELD,
      JDBC_PASSWORD_FIELD,
    ],
    pitfalls: ['必须用 gsjdbc200.jar（独立类名），避免与 PG 驱动冲突'],
  },
  {
    key: 'maxcompute',
    icon: '🏢',
    label: 'MaxCompute',
    description: '阿里云大数据计算服务，独立 JDBC（路标）',
    category: 'databases',
    maturity: 'GA',
    capabilities: ['explore', 'batch_sync'],
    defaultPort: '',
    keywords: ['云数仓', '阿里云', '大数据', '国产'],
    popularity: 6,
    usage: 5,
    configSchema: [
      { key: 'endpoint', label: 'Endpoint', placeholder: 'http://service.cn-hangzhou.maxcompute.aliyun.com/api' },
      { key: 'project', label: 'Project', required: true },
      { key: 'username', label: 'Access Key ID', flex: 1 },
      { key: 'password', label: 'Access Key Secret', type: 'password', flex: 1 },
    ],
    pitfalls: ['路标：独立 Trino connector 集成成本高，本期仅落地'],
  },
];

/** 按 connector_type 快速查找 */
export const CONNECTOR_META: Record<string, ConnectorMeta> = Object.fromEntries(
  CONNECTOR_CATALOG.map((c) => [c.key, c]),
);

/** 品类分组顺序（目录页展示用） */
export const CATEGORY_ORDER: ConnectorCategory[] = [
  'databases',
  'lakehouse',
  'file_storage',
  'streaming',
  'nosql',
  'generic',
];

export const CATEGORY_LABELS: Record<ConnectorCategory, string> = {
  databases: '数据库',
  lakehouse: '湖仓格式',
  file_storage: '文件与对象存储',
  streaming: '消息与流式',
  nosql: 'NoSQL',
  generic: '通用',
};

/** 能力标签中文映射（CapabilityBar 展示用） */
export const CAPABILITY_LABELS: Record<Capability, string> = {
  explore: '探索',
  batch_sync: '批量',
  cdc: 'CDC',
  virtual_table: '虚拟表',
  streaming_sync: '流式',
  file_sync: '文件',
};

/**
 * 可作为筛选维度的能力。
 *
 * `explore` 是所有连接器的基线能力（见测试 every connector has explore capability），
 * 作为筛选条件等于全选，无区分度，故从筛选条中排除。
 * 卡片上的能力标签仍会展示 explore（由 CAPABILITY_LABELS 驱动）。
 */
export const FILTERABLE_CAPABILITIES: Capability[] = [
  'batch_sync',
  'cdc',
  'virtual_table',
  'streaming_sync',
  'file_sync',
];

/**
 * 目录排序优先级：华为 > 业界流行度 > 业界使用量 > 产品成熟度。
 *
 * 排序键（从高到低）：
 *   1. vendor === 'huawei' 排前
 *   2. popularity 降序
 *   3. usage 降序
 *   4. maturity：GA > Beta > Alpha
 *   5. label 兜底（稳定排序）
 */
const MATURITY_RANK: Record<Maturity, number> = { GA: 0, Beta: 1, Alpha: 2 };

export function connectorSortRank(a: ConnectorMeta, b: ConnectorMeta): number {
  // 1. 华为优先
  const aHuawei = a.vendor === 'huawei' ? 0 : 1;
  const bHuawei = b.vendor === 'huawei' ? 0 : 1;
  if (aHuawei !== bHuawei) return aHuawei - bHuawei;
  // 2. 业界流行度
  const aPop = a.popularity ?? 0;
  const bPop = b.popularity ?? 0;
  if (aPop !== bPop) return bPop - aPop;
  // 3. 业界使用量
  const aUse = a.usage ?? 0;
  const bUse = b.usage ?? 0;
  if (aUse !== bUse) return bUse - aUse;
  // 4. 产品成熟度
  const aMat = MATURITY_RANK[a.maturity] ?? 99;
  const bMat = MATURITY_RANK[b.maturity] ?? 99;
  if (aMat !== bMat) return aMat - bMat;
  // 5. 兜底稳定排序
  return a.label.localeCompare(b.label, 'zh');
}
