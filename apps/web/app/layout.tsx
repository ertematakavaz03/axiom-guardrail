import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geist = Geist({ variable: "--font-geist", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-mono", subsets: ["latin"] });

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const imageUrl = `${protocol}://${host}/og.png`;
  const description = "Evidence-driven AI agent evaluation, safety, and release control.";
  return {
    applicationName: "Axiom Guardrail",
    title: "Axiom Guardrail — AI Agent Release Control",
    description,
    manifest: "/site.webmanifest",
    icons: {
      icon: [
        { url: "/favicon.ico", sizes: "any" },
        { url: "/favicon-16x16.png", type: "image/png", sizes: "16x16" },
        { url: "/favicon-32x32.png", type: "image/png", sizes: "32x32" },
      ],
      apple: [{ url: "/apple-touch-icon.png", type: "image/png", sizes: "180x180" }],
    },
    openGraph: { title: "Axiom Guardrail", siteName: "Axiom Guardrail", description, images: [{ url: imageUrl, width: 2048, height: 682, alt: "Axiom Guardrail" }] },
    twitter: { card: "summary_large_image", title: "Axiom Guardrail", description, images: [imageUrl] },
  };
}

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#07090d",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geist.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
