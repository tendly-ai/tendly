import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { AccountProvider } from "./context/AccountContext";
import Nav from "./components/Nav";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Tendly",
  description: "AI-powered care assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>
        <AccountProvider>
          <Nav />
          {children}
        </AccountProvider>
      </body>
    </html>
  );
}
