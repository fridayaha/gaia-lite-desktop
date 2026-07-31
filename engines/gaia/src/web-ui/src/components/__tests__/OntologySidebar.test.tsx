import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OntologySidebar } from '../OntologySidebar';
import type { Ontology } from '../../types';

const _ontologies: Ontology[] = [
  { id: 'o1', api_name: 'finance', display_name: '财务', status: 'ACTIVE', object_types_count: 3, ontology_id: '', space_id: '', created_at: '', updated_at: '' } as any,
  { id: 'o2', api_name: 'hr', display_name: '人力', status: 'DEPRECATED', object_types_count: 1, ontology_id: '', space_id: '', created_at: '', updated_at: '' } as any,
];

const noop = () => {};

describe('OntologySidebar resource management menu (design step 2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders ontology list', () => {
    render(
      <OntologySidebar
        ontologies={_ontologies}
        selectedOntology="finance"
        onSelectOntology={noop}
        onCreateOntology={noop}
        collapsed={false}
        onCollapsedChange={noop}
      />,
    );
    expect(screen.getByText('财务')).toBeInTheDocument();
    expect(screen.getByText('人力')).toBeInTheDocument();
  });

  it('shows management menu button when lifecycle callbacks provided', () => {
    render(
      <OntologySidebar
        ontologies={_ontologies}
        selectedOntology="finance"
        onSelectOntology={noop}
        onCreateOntology={noop}
        onDeprecateOntology={noop}
        onDeleteOntology={noop}
        collapsed={false}
        onCollapsedChange={noop}
      />,
    );
    expect(screen.getByLabelText('财务 管理')).toBeInTheDocument();
  });

  it('opens menu with deprecate/delete for active ontology', () => {
    render(
      <OntologySidebar
        ontologies={_ontologies}
        selectedOntology="finance"
        onSelectOntology={noop}
        onCreateOntology={noop}
        onDeprecateOntology={noop}
        onRestoreOntology={noop}
        onDeleteOntology={noop}
        collapsed={false}
        onCollapsedChange={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText('财务 管理'));
    // Active ontology → deprecate + delete, NOT restore.
    expect(screen.getByText('弃用')).toBeInTheDocument();
    expect(screen.getByText('删除')).toBeInTheDocument();
    expect(screen.queryByText('恢复')).not.toBeInTheDocument();
  });

  it('opens menu with restore for deprecated ontology', () => {
    render(
      <OntologySidebar
        ontologies={_ontologies}
        selectedOntology="hr"
        onSelectOntology={noop}
        onCreateOntology={noop}
        onDeprecateOntology={noop}
        onRestoreOntology={noop}
        onDeleteOntology={noop}
        collapsed={false}
        onCollapsedChange={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText('人力 管理'));
    // Deprecated ontology → restore + delete, NOT deprecate.
    expect(screen.getByText('恢复')).toBeInTheDocument();
    expect(screen.queryByText('弃用')).not.toBeInTheDocument();
  });

  it('hides menu items when permission denied', () => {
    const decisions = {
      finance: { allowedActions: [], disabledReasons: { 'ontology:edit': 'denied', 'ontology:delete': 'denied' } },
    };
    render(
      <OntologySidebar
        ontologies={_ontologies}
        selectedOntology="finance"
        onSelectOntology={noop}
        onCreateOntology={noop}
        onDeprecateOntology={noop}
        onDeleteOntology={noop}
        decisions={decisions}
        collapsed={false}
        onCollapsedChange={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText('财务 管理'));
    // No permission → deprecate and delete hidden (menu empty but still opens).
    expect(screen.queryByText('弃用')).not.toBeInTheDocument();
    expect(screen.queryByText('删除')).not.toBeInTheDocument();
  });

  it('calls onDeprecateOntology with api_name', () => {
    const onDeprecate = vi.fn();
    render(
      <OntologySidebar
        ontologies={_ontologies}
        selectedOntology="finance"
        onSelectOntology={noop}
        onCreateOntology={noop}
        onDeprecateOntology={onDeprecate}
        collapsed={false}
        onCollapsedChange={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText('财务 管理'));
    fireEvent.click(screen.getByText('弃用'));
    expect(onDeprecate).toHaveBeenCalledWith('finance');
  });
});
