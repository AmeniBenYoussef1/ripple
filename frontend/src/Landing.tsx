// Public landing page (filled in Part 2).
import { Link } from "react-router-dom";

export function Landing() {
  return (
    <main className="page" style={{ padding: 48 }}>
      <h1>Ripple</h1>
      <p>Drop one problem. Watch the ripple.</p>
      <Link className="btn btn-primary" to="/app">Try the demo</Link>
    </main>
  );
}
