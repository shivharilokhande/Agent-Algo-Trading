import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { api, RunOut } from "../api";

const SECTIONS = [
  ["market_report", "Market"], ["sentiment_report", "Sentiment"], ["news_report", "News"],
  ["fundamentals_report", "Fundamentals"], ["investment_plan", "Research Decision"],
  ["trader_investment_plan", "Trader Plan"], ["final_trade_decision", "Final Decision"],
] as const;

function md(s: string) {
  return { __html: DOMPurify.sanitize(marked.parse(s || "_no content_") as string) };
}

export default function Compare() {
  const [params] = useSearchParams();
  const [a, setA] = useState<RunOut | null>(null);
  const [b, setB] = useState<RunOut | null>(null);
  const [reportsA, setReportsA] = useState<Record<string, string>>({});
  const [reportsB, setReportsB] = useState<Record<string, string>>({});
  const [tab, setTab] = useState("final_trade_decision");

  useEffect(() => {
    const ida = params.get("a"), idb = params.get("b");
    if (!ida || !idb) return;
    (async () => {
      const [ra, rb, rra, rrb] = await Promise.all([
        api.get<RunOut>(`/api/runs/${ida}`),
        api.get<RunOut>(`/api/runs/${idb}`),
        api.get<any[]>(`/api/runs/${ida}/reports`),
        api.get<any[]>(`/api/runs/${idb}/reports`),
      ]);
      setA(ra); setB(rb);
      setReportsA(Object.fromEntries(rra.map((x) => [x.section, x.content_md])));
      setReportsB(Object.fromEntries(rrb.map((x) => [x.section, x.content_md])));
    })();
  }, [params]);

  if (!a || !b) return <p className="muted">Select two runs in <Link to="/runs">Run History</Link> to compare.</p>;

  function Head({ r }: { r: RunOut }) {
    const tokens = (r.stats.tokens_in ?? 0) + (r.stats.tokens_out ?? 0);
    return (
      <div className="card" style={{ marginBottom: 10 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <b>{r.ticker} · {r.trade_date}</b>
          {r.rating && <span className={`rating ${r.rating}`}>{r.rating}</span>}
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          {r.mode} · depth {String(r.config.research_depth)} · {String(r.config.llm_provider ?? r.mode)} ·
          {" "}{(tokens / 1000).toFixed(1)}k tokens · <Link to={`/runs/${r.id}`}>open</Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1>Compare Runs</h1>
      <p className="page-sub">{a.ticker} {a.trade_date} vs {b.ticker} {b.trade_date}</p>
      <div className="grid2">
        <Head r={a} /><Head r={b} />
      </div>
      <div className="tabs">
        {SECTIONS.map(([key, label]) =>
          (reportsA[key] || reportsB[key]) ? (
            <div key={key} className={`tab ${tab === key ? "active" : ""}`} onClick={() => setTab(key)}>{label}</div>
          ) : null
        )}
      </div>
      <div className="grid2">
        <div className="report" dangerouslySetInnerHTML={md(reportsA[tab])} />
        <div className="report" dangerouslySetInnerHTML={md(reportsB[tab])} />
      </div>
    </div>
  );
}
