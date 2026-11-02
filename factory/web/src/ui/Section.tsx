import { useId, type ReactNode } from "react";

// 見出し（h2）つきのまとまり。見出しが区画の名前になる
export function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section className="ui-section" aria-labelledby={id}>
      <h2 id={id} className="ui-section__title">
        {title}
      </h2>
      {children}
    </section>
  );
}
