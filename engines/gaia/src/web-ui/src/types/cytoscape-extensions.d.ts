declare module 'cytoscape-cxtmenu' {
  const ext: cytoscape.Ext;
  export default ext;
}

declare module 'cytoscape-navigator' {
  const ext: cytoscape.Ext;
  export default ext;
}

declare module 'cytoscape-fcose' {
  const ext: cytoscape.Ext;
  export default ext;
}

declare module 'cytoscape-svg' {
  const ext: cytoscape.Ext;
  export default ext;
}

declare namespace cytoscape {
  interface Core {
    /** cytoscape-svg 扩展：返回与画布渲染一致的序列化 SVG 字符串。 */
    svg(options?: { full?: boolean; scale?: number; bg?: string; maxWidth?: number; maxHeight?: number }): string;
  }
}
