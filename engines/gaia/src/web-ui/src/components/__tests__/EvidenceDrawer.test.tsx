/**
 * EvidenceDrawer 组件测试（证据链详情抽屉）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { EvidenceDrawer } from '../EvidenceDrawer';
import type { AnalysisRecord } from '../../types';

vi.mock('../../api/graph', () => ({
  getAnalysis: vi.fn(),
}));

import { getAnalysis } from '../../api/graph';
const mockGet = getAnalysis as ReturnType<typeof vi.fn>;

const record: AnalysisRecord = {
  id: 'rec-123',
  ontology_id: 'ont-abc',
  principal: 'anonymous',
  object_set_ir: { type: 'objectType', object_type: 'Supplier' } as never,
  result_summary: {
    steps: 2,
    engines_used: ['neo4j', 'postgres'],
    timings: { object_type: 5, traverse: 12 },
    total_vids: 4,
    hydrated: 4,
    steps_detail: [
      { step: 'object_type', engine: 'postgres', elapsed: 5, count: 2 },
      { step: 'traverse', engine: 'neo4j', elapsed: 12, count: 4 },
    ],
  },
  evidence_pointers: { matched_vids: ['S001', 'S002'], object_count: 2 },
  created_at: '2026-07-02T10:00:00Z',
} as unknown as AnalysisRecord;

describe('EvidenceDrawer', () => {
  beforeEach(() => vi.clearAllMocks());

  it('analysisId 为 null 时不渲染', () => {
    const { container } = render(<EvidenceDrawer ontology="ONT" analysisId={null} onClose={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it('加载并展示证据链详情', async () => {
    mockGet.mockResolvedValue(record);
    render(<EvidenceDrawer ontology="ONT" analysisId="rec-123" onClose={() => {}} />);

    expect(screen.getByText('加载中…')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('证据链详情')).toBeInTheDocument();
    });
    // 基本信息
    expect(screen.getByText('ont-abc')).toBeInTheDocument();
    expect(screen.getByText('anonymous')).toBeInTheDocument();
    // 引擎标签
    expect(screen.getAllByText('neo4j').length).toBeGreaterThan(0);
    expect(screen.getAllByText('postgres').length).toBeGreaterThan(0);
    // 步骤明细
    expect(screen.getByText('object_type')).toBeInTheDocument();
    expect(screen.getByText('traverse')).toBeInTheDocument();
    // 证据指针 rid
    expect(screen.getByText('S001')).toBeInTheDocument();
    expect(screen.getByText('S002')).toBeInTheDocument();
  });

  it('API 失败时显示错误', async () => {
    mockGet.mockRejectedValue(new Error('记录不存在'));
    render(<EvidenceDrawer ontology="ONT" analysisId="bad" onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/记录不存在/)).toBeInTheDocument();
    });
  });

  it('点击关闭按钮调 onClose', async () => {
    mockGet.mockResolvedValue(record);
    const onClose = vi.fn();
    render(<EvidenceDrawer ontology="ONT" analysisId="rec-123" onClose={onClose} />);

    await waitFor(() => expect(screen.getByLabelText('关闭')).toBeInTheDocument());
    screen.getByLabelText('关闭').click();
    expect(onClose).toHaveBeenCalledOnce();
  });
});
