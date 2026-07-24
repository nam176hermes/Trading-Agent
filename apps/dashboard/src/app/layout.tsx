import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trading Agent",
  description: "Autonomous trading agent dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-zinc-950 text-zinc-100 min-h-screen">
        {children}
      </body>
    </html>
  );
}
