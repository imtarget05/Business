import type { Metadata, Viewport } from "next";
import Sidebar from "@/components/Sidebar";
import MobileMenu from "@/components/MobileMenu";
import "./globals.css";

export const metadata: Metadata = {
  title: "Business Ops Agent Swarm",
  description: "Multi-agent platform for business operations",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <div className="flex min-h-screen">
          <Sidebar />
          <MobileMenu />
          <main className="flex-1 p-4 md:p-8">{children}</main>
        </div>
      </body>
    </html>
  );
}