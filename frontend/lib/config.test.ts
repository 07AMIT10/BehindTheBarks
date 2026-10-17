import { describe, expect, it } from "vitest";
import { backendBase } from "./config";

describe("backendBase", () => {
  it("defaults to page host on port 8000", () => {
    expect(backendBase({ search: "", env: "", protocol: "http:", hostname: "laptop.local" })).toEqual({
      http: "http://laptop.local:8000", ws: "ws://laptop.local:8000",
    });
  });
  it("env var wins over default, https maps to wss", () => {
    expect(backendBase({ search: "", env: "https://abc.trycloudflare.com/", protocol: "http:", hostname: "x" })).toEqual({
      http: "https://abc.trycloudflare.com", ws: "wss://abc.trycloudflare.com",
    });
  });
  it("?backend= wins over everything and accepts ws urls", () => {
    expect(backendBase({ search: "?backend=wss%3A%2F%2Fb.example", env: "http://e", protocol: "http:", hostname: "x" })).toEqual({
      http: "https://b.example", ws: "wss://b.example",
    });
  });
  it("a page served by the backend on another port is its own backend", () => {
    // Same-origin is what the households cookies and the v2 invite links need (T2.3/T2.6).
    expect(backendBase({
      search: "", env: "", location: { origin: "http://192.168.1.20:8001", port: "8001", protocol: "http:", hostname: "192.168.1.20", search: "" },
    })).toEqual({ http: "http://192.168.1.20:8001", ws: "ws://192.168.1.20:8001" });
  });
  it("next dev on :3000 still falls back to :8000", () => {
    expect(backendBase({
      search: "", env: "", location: { origin: "http://localhost:3000", port: "3000", protocol: "http:", hostname: "localhost", search: "" },
    })).toEqual({ http: "http://localhost:8000", ws: "ws://localhost:8000" });
  });
  it("https protocol uses standard origin without port 8000", () => {
    expect(backendBase({ search: "", env: "", protocol: "https:", hostname: "behind-the-barks.onrender.com" })).toEqual({
      http: "https://behind-the-barks.onrender.com", ws: "wss://behind-the-barks.onrender.com",
    });
  });
});
