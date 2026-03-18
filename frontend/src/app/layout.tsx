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
        <header className="fixed top-3 left-4 right-4 z-50 glass-bar rounded-2xl">
          <div className="flex items-center justify-between px-5 py-2.5">
            <div className="flex items-center gap-2.5">
              <span className="inline-block h-2 w-2 rounded-full bg-accent" />
              <span className="font-mono text-sm font-bold tracking-tight text-text-primary">
                FakeNewsGuard
              </span>
            </div>
            <ThemeToggle />
          </div>
        </header>

        <main className="pt-16">{children}</main>
      </body>
    </html>
  );
}
