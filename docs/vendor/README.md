# Bundled third-party code

`anthropic-sdk.js` is the official Anthropic TypeScript SDK, `@anthropic-ai/sdk` version 1.8.0,
bundled into a single browser module so the page never loads code from a third-party server.
It is licensed under the Apache License 2.0; see `LICENSE-anthropic-sdk.txt`.

To rebuild after an SDK update:

    npm install @anthropic-ai/sdk esbuild
    printf 'import Anthropic from "@anthropic-ai/sdk";
export default Anthropic;
' > entry.js
    npx esbuild entry.js --bundle --format=esm --minify --platform=browser --target=es2020 --legal-comments=eof --outfile=anthropic-sdk.js
