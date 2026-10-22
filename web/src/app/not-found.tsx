import Link from "next/link";

export default function NotFound() {
  return (
    <div className="card empty">
      <h1 className="page-title" style={{ marginBottom: 8 }}>
        Nothing here
      </h1>
      <p style={{ marginBottom: 16 }}>That page does not exist.</p>
      <Link href="/" className="btn">
        Back to the event tape
      </Link>
    </div>
  );
}
