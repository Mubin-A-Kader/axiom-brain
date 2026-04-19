import type { Metadata } from "next";
import { Instrument_Sans, DM_Sans, Fira_Code } from "next/font/google";
import "./globals.css";
import { SidebarWrapper } from "../components/SidebarWrapper";

const headerFont = Instrument_Sans({
  variable: "--font-header",
  subsets: ["latin"],
});

const bodyFont = DM_Sans({
  variable: "--font-body",
  subsets: ["latin"],
});

const monoFont = Fira_Code({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Axiom | Tactile Studio",
  description: "Reasoning-as-Infrastructure platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // We can't conditionally render client components easily in a server layout based on path without 'headers' hack.
  // Instead, we'll let the Sidebar render, but we need to ensure it's not shown on the login page.
  // The best way in App Router is to move Sidebar down a level or use a route group.
  // For simplicity, we'll keep it here and hide it via CSS if the path is /login. 
  // Wait, Next.js server components don't have access to pathname. 
  // Let's move Sidebar to a Client Component wrapper or just leave it. The login page will cover the screen.
  
  return (
    <html
      lang="en"
      className={`${headerFont.variable} ${bodyFont.variable} ${monoFont.variable} h-full dark`}
      suppressHydrationWarning
    >
      <body 
        className="min-h-full flex bg-[#1E1E1C] text-[#E6E1D8] selection:bg-[#638A70] selection:text-[#1E1E1C]"
        suppressHydrationWarning
      >
        <SidebarWrapper>
          {children}
        </SidebarWrapper>
      </body>
    </html>
  );
}
