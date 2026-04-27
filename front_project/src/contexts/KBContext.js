import React, { createContext, useContext, useMemo, useState, useCallback } from 'react';

const KBContext = createContext(null);
const DEFAULT_KB_NAME = 'Obsidian Vault';
const DEFAULT_KB_ID = 1;

export function KBProvider({ children }) {
  const [kbName, setKbName] = useState(DEFAULT_KB_NAME);
  const [kbId, setKbId] = useState(DEFAULT_KB_ID);
  const [selectedDocIds, setSelectedDocIds] = useState([]);

  const clearDocs = useCallback(() => setSelectedDocIds([]), []);

  const value = useMemo(
    () => ({
      kbName,
      setKbName,
      kbId,
      setKbId,
      selectedDocIds,
      setSelectedDocIds,
      clearDocs,
    }),
    [kbName, kbId, selectedDocIds, clearDocs],
  );

  return <KBContext.Provider value={value}>{children}</KBContext.Provider>;
}

export function useKB() {
  return useContext(KBContext);
}

export default KBProvider;
