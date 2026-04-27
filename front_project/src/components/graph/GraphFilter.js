import React from 'react';

export default function GraphFilter({
  graphSort,
  setGraphSort,
  searchTerm,
  setSearchTerm,
  onSearch,
}) {
  const getSelected = () => {
    if (!graphSort) return [];
    return graphSort.replace(/-term$/, '').split('-');
  };

  const toggleSort = (value) => {
    const selected = getSelected();
    let next;
    if (selected.includes(value)) {
      next = selected.filter((v) => v !== value);
    } else {
      next = [...selected, value];
    }
    if (next.length === 0) {
      setGraphSort('');
    } else {
      setGraphSort(next.join('-') + '-term');
    }
  };

  const selected = getSelected();

  return (
    <div className="graph-filter">
      <div className="graph-filter-row graph-filter-row--compact">
        <label className="graph-label">Enter search term</label>
        <input
          className="graph-input"
          placeholder="ex) APM, MOSAR"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
        <button className="graph-button" onClick={onSearch}>
          Search
        </button>
      </div>

      <div className="graph-filter-row checkbox-row">
        <label>
          <input
            type="checkbox"
            value="category"
            checked={selected.includes('category')}
            onChange={() => toggleSort('category')}
          />
          category
        </label>

        <label>
          <input
            type="checkbox"
            value="document"
            checked={selected.includes('document')}
            onChange={() => toggleSort('document')}
          />
          document
        </label>

        <label>
          <input
            type="checkbox"
            value="chunk"
            checked={selected.includes('chunk')}
            onChange={() => toggleSort('chunk')}
          />
          chunk
        </label>
      </div>
    </div>
  );
}
