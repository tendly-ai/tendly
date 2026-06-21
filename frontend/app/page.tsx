import Link from "next/link";

export default function Home() {
  return (
    <main style={{ maxWidth: 720, margin: "80px auto", padding: 24 }}>
      <h1 style={{ fontSize: 40 }}>Tendly</h1>
      <p style={{ fontSize: 20, color: "#475067" }}>
        AI-powered elderly care assistant.
      </p>
      <div style={{ display: "flex", gap: 16, marginTop: 32 }}>
        <Link
          href="/patient"
          style={{
            padding: "20px 28px",
            background: "#2563eb",
            color: "white",
            borderRadius: 12,
            fontSize: 22,
            textDecoration: "none",
          }}
        >
          Patient Interface →
        </Link>
        <Link
          href="/dashboard"
          style={{
            padding: "20px 28px",
            background: "#0f766e",
            color: "white",
            borderRadius: 12,
            fontSize: 22,
            textDecoration: "none",
          }}
        >
          Caregiver Dashboard →
        </Link>
      </div>
    </main>
  );
}
