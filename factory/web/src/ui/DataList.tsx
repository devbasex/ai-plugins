import type { ReactNode } from "react";
import { Link } from "react-router";

export interface DataColumn<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
}

// 一覧。768 px 以上は列の表、未満は 1 行を 1 枚のカードに積む（CSS だけで切り替える）
// rowHref を渡すと 1 列目が詳細へのリンクになり、行のどこを押しても移る
export function DataList<T>({
  label,
  columns,
  rows,
  rowKey,
  rowHref,
}: {
  label: string;
  columns: DataColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  rowHref?: (row: T) => string;
}) {
  return (
    <table className="ui-datalist" aria-label={label}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key} scope="col">
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)} className={rowHref ? "ui-datalist__row ui-datalist__row--link" : "ui-datalist__row"}>
            {columns.map((c, i) => (
              <td key={c.key} data-label={c.header}>
                {i === 0 && rowHref ? (
                  <Link className="ui-datalist__link" to={rowHref(row)}>
                    {c.render(row)}
                  </Link>
                ) : (
                  c.render(row)
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
