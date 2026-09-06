import "./styles.css";
import "./retry.css";
import "./thesis.css";
import "./supply-chain.css";
import "./debate.css";
import "./autonomous.css";
import "./arena.css";
import "./console.css";
import "./trace-console.css";
import "./admin-enhancements.css";
export const metadata = { title: "SignalForge", description: "Evidence-first AI investment research" };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="zh-Hant"><body><nav className="poc-nav"><a href="/">01 Earnings</a><a href="/thesis">02 Thesis</a><a href="/supply-chain">03 Supply Chain</a><a href="/debate">04 Debate</a><a href="/autonomous">05 Autonomous</a><a href="/arena">06 Arena</a><a href="/console">Console</a></nav>{children}</body></html>;
}
