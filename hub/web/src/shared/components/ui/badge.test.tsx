import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge } from "./badge";

describe("badge", () => {
  it("renders its children", () => {
    render(<Badge>运行中</Badge>);
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("merges a custom className onto the base style", () => {
    render(<Badge className="text-red-500">退出 1</Badge>);
    const el = screen.getByText("退出 1");
    expect(el).toHaveClass("text-red-500");
    expect(el).toHaveClass("inline-flex"); // base style still applied
  });
});
