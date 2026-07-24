'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

import {
  INITIAL_SAFETY_AUTHORITY_STATE,
  UNAVAILABLE_OPERATOR_STATE,
  advanceSafetyAuthorityState,
  createLatestStateCoordinator,
  loadOperatorState,
  type OperatorState,
  type SafetyAuthorityState,
} from '@/lib/trading/operator-state';

interface OperatorStateContextValue extends Readonly<OperatorState> {
  safetyRevision: number;
  refresh: () => Promise<void>;
  invalidateSafetyState: () => void;
}

const OperatorStateContext = createContext<OperatorStateContextValue | null>(null);

export function OperatorStateProvider({ children }: { children: React.ReactNode }) {
  const [authority, setAuthority] = useState<Readonly<SafetyAuthorityState>>(
    INITIAL_SAFETY_AUTHORITY_STATE,
  );
  const coordinator = useMemo(() => createLatestStateCoordinator({
    load: loadOperatorState,
    publish: (nextState) => {
      setAuthority((current) => advanceSafetyAuthorityState(current, nextState));
    },
  }), []);

  const refresh = useCallback(async () => {
    await coordinator.resume();
  }, [coordinator]);

  const invalidateSafetyState = useCallback(() => {
    coordinator.invalidate(UNAVAILABLE_OPERATOR_STATE);
  }, [coordinator]);

  useEffect(() => {
    void coordinator.resume();
    const interval = window.setInterval(() => void coordinator.run(), 15_000);
    return () => {
      window.clearInterval(interval);
      coordinator.cancel();
    };
  }, [coordinator]);

  const value = useMemo<OperatorStateContextValue>(() => ({
    ...authority.state,
    safetyRevision: authority.safetyRevision,
    refresh,
    invalidateSafetyState,
  }), [authority, invalidateSafetyState, refresh]);

  return (
    <OperatorStateContext.Provider value={value}>
      {children}
    </OperatorStateContext.Provider>
  );
}

export function useOperatorState(): OperatorStateContextValue {
  const state = useContext(OperatorStateContext);
  if (state === null) {
    throw new Error('useOperatorState must be used within OperatorStateProvider');
  }
  return state;
}
