import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import Link from "next/link";
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
        <header className="fixed top-3 left-4 right-4 z-50 glass-bar rounded-2xl">
          <div className="flex items-center justify-between px-5 py-2.5">
            <Link href="/" className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
              <svg width="20" height="24" viewBox="0 0 20 24" fill="none" xmlns="http://www.w3.org/2000/svg" className="shrink-0">
                {/* Shield outline */}
                <path d="M10 1L2 4.5V10.5C2 16 5.5 21 10 23C14.5 21 18 16 18 10.5V4.5L10 1Z" stroke="var(--accent)" strokeWidth="1.5" strokeLinejoin="round" fill="none" />
                {/* Neural network connections */}
                <line x1="10" y1="6" x2="6.5" y2="10" stroke="var(--accent)" strokeWidth="0.8" opacity="0.5" />
                <line x1="10" y1="6" x2="13.5" y2="10" stroke="var(--accent)" strokeWidth="0.8" opacity="0.5" />
                <line x1="6.5" y1="10" x2="8" y2="15" stroke="var(--accent)" strokeWidth="0.8" opacity="0.5" />
                <line x1="13.5" y1="10" x2="12" y2="15" stroke="var(--accent)" strokeWidth="0.8" opacity="0.5" />
                <line x1="6.5" y1="10" x2="13.5" y2="10" stroke="var(--accent)" strokeWidth="0.8" opacity="0.3" />
                <line x1="8" y1="15" x2="12" y2="15" stroke="var(--accent)" strokeWidth="0.8" opacity="0.3" />
                <line x1="10" y1="6" x2="10" y2="18" stroke="var(--accent)" strokeWidth="0.8" opacity="0.2" />
                {/* Neural nodes */}
                <circle cx="10" cy="6" r="1.5" fill="var(--accent)" />
                <circle cx="6.5" cy="10" r="1.3" fill="var(--accent)" opacity="0.85" />
                <circle cx="13.5" cy="10" r="1.3" fill="var(--accent)" opacity="0.85" />
                <circle cx="8" cy="15" r="1.2" fill="var(--accent)" opacity="0.7" />
                <circle cx="12" cy="15" r="1.2" fill="var(--accent)" opacity="0.7" />
                <circle cx="10" cy="18" r="1" fill="var(--accent)" opacity="0.5" />
              </svg>
              <span className="font-mono text-sm font-bold tracking-tight text-text-primary">
                FakeNewsGuard
              </span>
            </Link>
            <div className="flex items-center gap-4">
              <Link
                href="/archiv"
                className="font-mono text-xs text-text-tertiary hover:text-text-primary transition-colors"
              >
                Archiv
              </Link>
              <ThemeToggle />
            </div>
          </div>
        </header>

        <main className="pt-16">{children}</main>
      </body>
    </html>
  );
}
