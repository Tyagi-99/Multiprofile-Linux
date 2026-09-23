import next from "eslint-config-next";

export default [
  ...next,
  {
    rules: {
      // Design-system enforcement: raw hex colors in classNames/TSS are a
      // review flag — values belong in src/styles/tokens.css.
      "no-restricted-syntax": [
        "warn",
        {
          selector: "Literal[value=/^#[0-9a-fA-F]{3,8}$/]",
          message: "Raw hex color — use a design token from tokens.css instead.",
        },
      ],
    },
  },
];
