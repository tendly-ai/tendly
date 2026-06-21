import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tendly",
  description: "AI-powered elderly care assistant",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
