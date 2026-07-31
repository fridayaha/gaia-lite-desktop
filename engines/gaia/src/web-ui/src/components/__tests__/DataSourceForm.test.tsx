import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  CONNECTOR_CATALOG,
  CONNECTOR_META,
  CATEGORY_ORDER,
  CATEGORY_LABELS,
  CAPABILITY_LABELS,
  FILTERABLE_CAPABILITIES,
  connectorSortRank,
} from '../../constants/connectorCatalog';

describe('connectorCatalog data integrity', () => {
  it('every connector has a unique key', () => {
    const keys = CONNECTOR_CATALOG.map((c) => c.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('华为系连接器已标注 vendor=huawei', () => {
    const huawei = CONNECTOR_CATALOG.filter((c) => c.vendor === 'huawei').map((c) => c.key);
    expect(huawei.sort()).toEqual(['gaussdb', 'gaussdb_dws', 'opengauss']);
  });

  it('connectorSortRank：华为优先于非华为', () => {
    const mysql = CONNECTOR_META['mysql'];
    const opengauss = CONNECTOR_META['opengauss'];
    // MySQL 流行度更高，但华为系应排前
    expect(connectorSortRank(opengauss, mysql)).toBeLessThan(0);
  });

  it('connectorSortRank：同为非华为时按流行度降序', () => {
    const mysql = CONNECTOR_META['mysql'];
    const tidb = CONNECTOR_META['tidb'];
    // MySQL popularity=10 > TiDB popularity=8
    expect(connectorSortRank(mysql, tidb)).toBeLessThan(0);
  });

  it('connectorSortRank：流行度相同时按使用量降序', () => {
    // PostgreSQL popularity=10 usage=9，MySQL popularity=10 usage=10
    const mysql = CONNECTOR_META['mysql'];
    const pg = CONNECTOR_META['postgresql'];
    expect(connectorSortRank(mysql, pg)).toBeLessThan(0);
  });

  it('connectorSortRank：流行度/使用量相同时按成熟度 GA>Beta>Alpha', () => {
    // 构造同分虚拟连接器，仅 maturity 不同，验证 GA 排前
    const ga = { ...CONNECTOR_META['mysql'], maturity: 'GA' as const, popularity: 5, usage: 5 };
    const beta = { ...CONNECTOR_META['mysql'], label: 'ZZZ', maturity: 'Beta' as const, popularity: 5, usage: 5 };
    const alpha = { ...CONNECTOR_META['mysql'], label: 'YYY', maturity: 'Alpha' as const, popularity: 5, usage: 5 };
    expect(connectorSortRank(ga, beta)).toBeLessThan(0);
    expect(connectorSortRank(beta, alpha)).toBeLessThan(0);
  });

  it('connectorSortRank：同分时按 label 兑底稳定排序', () => {
    const dameng = CONNECTOR_META['dameng'];
    const kingbase = CONNECTOR_META['kingbase'];
    // 两者均非华为、popularity=5 usage=5、maturity=Beta，同分兑底按 label
    // 「达梦」<「人大」，所以 dameng 应排前
    expect(connectorSortRank(dameng, kingbase)).toBeLessThan(0);
  });

  it('every connector has explore capability (baseline)', () => {
    for (const c of CONNECTOR_CATALOG) {
      expect(c.capabilities).toContain('explore');
    }
  });

  it('CONNECTOR_META lookup covers all catalog entries', () => {
    for (const c of CONNECTOR_CATALOG) {
      expect(CONNECTOR_META[c.key]).toBeDefined();
      expect(CONNECTOR_META[c.key].label).toBe(c.label);
    }
  });

  it('relational native dbs have cdc + virtual_table', () => {
    for (const ct of ['mysql', 'postgresql', 'opengauss', 'gaussdb', 'tidb']) {
      const caps = CONNECTOR_META[ct].capabilities;
      expect(caps).toContain('cdc');
      expect(caps).toContain('virtual_table');
    }
  });

  it('starrocks supports virtual_table + batch_sync', () => {
    const meta = CONNECTOR_META['starrocks'];
    expect(meta).toBeDefined();
    expect(meta.capabilities).toContain('virtual_table');
    expect(meta.capabilities).toContain('batch_sync');
    expect(meta.mysqlProto).toBe(true);
    expect(meta.defaultPort).toBe('9030');
  });

  it('elasticsearch is landing-only (strict one-cut, decision 4)', () => {
    const caps = CONNECTOR_META['elasticsearch'].capabilities;
    expect(caps).not.toContain('virtual_table');
    expect(caps).toContain('batch_sync');
  });

  it('file/object storage has no virtual_table', () => {
    for (const ct of ['s3', 'minio', 'oss', 'hdfs']) {
      expect(CONNECTOR_META[ct].capabilities).not.toContain('virtual_table');
    }
  });

  it('dameng and generic_jdbc have no virtual_table (no Gravitino provider)', () => {
    expect(CONNECTOR_META['dameng'].capabilities).not.toContain('virtual_table');
    expect(CONNECTOR_META['generic_jdbc'].capabilities).not.toContain('virtual_table');
  });

  it('CATEGORY_ORDER and CATEGORY_LABELS cover all categories', () => {
    const cats = new Set(CONNECTOR_CATALOG.map((c) => c.category));
    for (const cat of cats) {
      expect(CATEGORY_ORDER).toContain(cat);
      expect(CATEGORY_LABELS[cat]).toBeDefined();
    }
  });

  it('every config field has a key and label', () => {
    for (const c of CONNECTOR_CATALOG) {
      for (const f of c.configSchema) {
        expect(f.key).toBeTruthy();
        expect(f.label).toBeTruthy();
      }
    }
  });

  it('CAPABILITY_LABELS covers all capability values used', () => {
    const usedCaps = new Set<string>();
    for (const c of CONNECTOR_CATALOG) {
      for (const cap of c.capabilities) usedCaps.add(cap);
    }
    for (const cap of usedCaps) {
      expect(CAPABILITY_LABELS[cap as keyof typeof CAPABILITY_LABELS]).toBeDefined();
    }
  });

  it('FILTERABLE_CAPABILITIES 排除 explore（基线能力，无区分度）', () => {
    expect(FILTERABLE_CAPABILITIES).not.toContain('explore');
    // 其余 5 个能力应都在筛选维度中
    expect(FILTERABLE_CAPABILITIES.sort()).toEqual(
      ['batch_sync', 'cdc', 'file_sync', 'streaming_sync', 'virtual_table'].sort(),
    );
  });
});

describe('DataSourceForm connector catalog', () => {
  it('renders connector catalog with category rail and tiles', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    // 品类导航 rail：6 个品类按钮（无「全部」）
    expect(screen.getByRole('button', { name: /^数据库（/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^文件与对象存储（/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^消息与流式（/ })).toBeInTheDocument();
    // 右侧分组展示所有品类 + 连接器
    expect(screen.getByText('MySQL')).toBeInTheDocument();
    expect(screen.getByText('PostgreSQL')).toBeInTheDocument();
    expect(screen.getByText('Kafka')).toBeInTheDocument();
    // 品类标题（h4, .connector-category-title）应出现
    const headings = screen.getAllByText('数据库');
    expect(headings.length).toBeGreaterThanOrEqual(1);
    const streamingHeadings = screen.getAllByText('消息与流式');
    expect(streamingHeadings.length).toBeGreaterThanOrEqual(1);
  });

  it('shows all connectors grouped by category (no filtering by default)', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    // 全量目录每个连接器都应出现
    for (const c of CONNECTOR_CATALOG) {
      expect(screen.getByText(c.label)).toBeInTheDocument();
    }
  });

  it('catalog uses left rail + right grouped list layout structure', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    const { container } = render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    // 关键布局容器存在
    expect(container.querySelector('.connector-catalog-layout')).toBeInTheDocument();
    expect(container.querySelector('.connector-rail')).toBeInTheDocument();
    expect(container.querySelector('.connector-grid')).toBeInTheDocument();
    // rail 含各品类（共 CATEGORY_ORDER.length 个按钮，无「全部」）
    expect(container.querySelectorAll('.connector-rail .rail-btn').length).toBe(CATEGORY_ORDER.length);
    // 品类 section 分组存在
    expect(container.querySelector('.connector-category-section')).toBeInTheDocument();
    expect(container.querySelector('.connector-category-title')).toBeInTheDocument();
    // 瓷砖含 maturity / caps 标记
    expect(container.querySelector('.connector-tile')).toBeInTheDocument();
    expect(container.querySelector('.connector-tile-maturity')).toBeInTheDocument();
    expect(container.querySelector('.connector-tile-cap')).toBeInTheDocument();
  });

  it('tiles are sorted by 华为 > 流行度 > 使用量 > 成熟度 within each category section', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    const { container } = render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    // 检查每个品类 section 内的 tile 按 connectorSortRank 排序
    const sections = container.querySelectorAll('.connector-category-section');
    for (const section of sections) {
      const keys = Array.from(section.querySelectorAll('.connector-tile')).map((el) =>
        (el.querySelector('.connector-tile-label')?.textContent || '').trim(),
      );
      const metas = keys
        .map((label) => CONNECTOR_CATALOG.find((c) => c.label === label))
        .filter((m): m is NonNullable<typeof m> => m !== undefined);
      const sorted = [...metas].sort(connectorSortRank);
      expect(metas.map((m) => m.key)).toEqual(sorted.map((m) => m.key));
    }
  });

  it('华为系连接器排在数据库品类最前', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    const { container } = render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    const dbSection = Array.from(container.querySelectorAll('.connector-category-section')).find((s) =>
      (s.querySelector('.connector-category-title')?.textContent || '').includes('数据库'),
    );
    expect(dbSection).toBeTruthy();
    const firstLabels = Array.from(dbSection!.querySelectorAll('.connector-tile-label'))
      .slice(0, 3)
      .map((e) => (e.textContent || '').trim());
    // 数据库品类中华为系（OpenGauss / GaussDB / GaussDB DWS）应占据前 3
    const huaweiKeys = ['OpenGauss', 'GaussDB', 'GaussDB DWS'];
    for (const label of firstLabels) {
      expect(huaweiKeys).toContain(label);
    }
  });

  it('clicking a category rail scrolls to the section (does NOT filter)', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    // jsdom doesn't implement scrollIntoView, so we polyfill it
    Element.prototype.scrollIntoView = () => {};
    render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    // 点击「消息与流式」品类（模拟用户定位）
    fireEvent.click(screen.getByRole('button', { name: /^消息与流式（/ }));
    // 品类导航仅作定位，不过滤 — MySQL 和 Kafka 都应仍可见
    expect(screen.getByText('MySQL')).toBeInTheDocument();
    expect(screen.getByText('Kafka')).toBeInTheDocument();
  });

  it('filters connectors by search text (matches label/description/keywords)', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    const searchInput = screen.getByLabelText('搜索连接器') as HTMLInputElement;
    expect(searchInput).toBeTruthy();
    fireEvent.change(searchInput, { target: { value: 'kafka' } });
    // Kafka 卡片应出现，MySQL 应消失
    expect(screen.getByText('Kafka')).toBeInTheDocument();
    expect(screen.queryByText('MySQL')).toBeNull();
  });

  it('search matches keywords aliases (e.g. 「对象存储」)', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    const searchInput = screen.getByLabelText('搜索连接器') as HTMLInputElement;
    // 搜口语化别名「对象存储」应命中 s3/minio/oss（均含该 keyword）
    fireEvent.change(searchInput, { target: { value: '对象存储' } });
    expect(screen.getByText('Amazon S3')).toBeInTheDocument();
    expect(screen.getByText('MinIO')).toBeInTheDocument();
    expect(screen.getByText('阿里云 OSS')).toBeInTheDocument();
    // 不含该 keyword 的连接器应消失
    expect(screen.queryByText('MySQL')).toBeNull();
  });

  it('筛选条不含「探索」按钮（基线能力无区分度）', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    // 5 个可筛选能力按钮应存在
    for (const cap of FILTERABLE_CAPABILITIES) {
      expect(screen.getByRole('button', { name: CAPABILITY_LABELS[cap] })).toBeInTheDocument();
    }
    // 「探索」不应作为筛选按钮出现
    expect(screen.queryByRole('button', { name: '探索' })).toBeNull();
  });

  it('clicking a connector advances to the config step', async () => {
    const { DataSourceForm } = await import('../DataSourceForm');
    render(
      <DataSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );
    fireEvent.click(screen.getByText('MySQL'));
    // Step 2 标题
    expect(screen.getByText('连接 MySQL')).toBeInTheDocument();
    // MySQL 配置字段（host/port/database）
    expect(screen.getByText('主机')).toBeInTheDocument();
    expect(screen.getByText('端口')).toBeInTheDocument();
  });
});
