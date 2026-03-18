import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import ThemeToggle from "./components/ThemeToggle";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "FakeNewsGuard",
  description: "KI-gestützter Faktencheck für Nachrichten und Behauptungen",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="de" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                var t = localStorage.getItem('theme');
                var d = t === 'dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches);
                if (d) document.documentElement.classList.add('dark');
              })();
            `,
          }}
        />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased min-h-screen bg-bg-primary`}
      >
        <header className="fixed top-0 left-0 right-0 z-50 bg-bg-primary" style={{ boxShadow: "var(--shadow-header)" }}>
          <div className="h-[2px] bg-accent" />
          <div className="flex items-center justify-between px-6 py-3">
            <span className="font-mono text-sm font-bold tracking-tight text-text-primary">
              FakeNewsGuard
            </span>
            <ThemeToggle />
          </div>
        </header>

        <main className="pt-[calc(2px+2.75rem)]">{children}</main>
      </body>
    </html>
  );
}
