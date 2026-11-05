// つながりの状態。オフラインと確定待ちは別の値で持つ（オフラインで確定待ちがある場面も表せる）
export interface ConnectionState {
  online: boolean;
  lastReceivedAt: string | null;
  pendingOperations: number;
}

export interface ConnectionSource {
  current(): ConnectionState;
  subscribe(listener: () => void): () => void;
}

// ブラウザの実装は online だけを持つ。最後に受け取った時刻と確定待ちの数は #2383 が作る
export function browserConnection(win: Window = window): ConnectionSource {
  let state: ConnectionState = { online: win.navigator.onLine, lastReceivedAt: null, pendingOperations: 0 };
  return {
    current: () => state,
    subscribe(listener) {
      const update = () => {
        state = { ...state, online: win.navigator.onLine };
        listener();
      };
      win.addEventListener("online", update);
      win.addEventListener("offline", update);
      return () => {
        win.removeEventListener("online", update);
        win.removeEventListener("offline", update);
      };
    },
  };
}

// 決まった状態を返す実装。テストと、状態を切り替えて見るときに使う
export function fixedConnection(initial: ConnectionState): ConnectionSource & { set(next: ConnectionState): void } {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    current: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    set(next) {
      state = next;
      for (const l of listeners) l();
    },
  };
}
