# Future work: category-based email routing

## Goal

Extend the classifier so it can route non-spam mail into specific folders based on category, such as:

- News
- Shopping
- Newsletters
- Transactional
- Marketing
- Personal

## Why

The current classifier is optimized for binary spam detection. That works well for separating obvious spam from legitimate mail, but it does not yet help organize legitimate subscriptions and list mail into dedicated folders.

## Requirements

- Keep spam detection conservative and low false-positive.
- Make non-spam classification optional and configurable.
- Support mapping categories to IMAP folders.
- Preserve existing behavior for users who only want spam filtering.
- Preserve read/unread behavior and scanned flags.

## Proposed implementation steps

1. Update the Ollama prompt to return structured JSON with:
   - `is_spam`
   - `category`
   - `reason`

2. Define a default set of categories, for example:
   - `personal`
   - `news`
   - `shopping`
   - `newsletter`
   - `transactional`
   - `marketing`
   - `other`

3. Add configuration support for category-to-folder mappings in the YAML config, for example:

```yaml
settings:
  category_folders:
    newsletter: Newsletters
    shopping: Shopping
    news: News
```

4. Update the mail processing flow so that:
   - spam messages are moved to the spam folder
   - non-spam messages can be moved to the mapped category folder when configured
   - messages that do not match a configured category remain in the inbox

5. Add regression tests for:
   - spam detection
   - category detection
   - folder mapping
   - fallback behavior when no category mapping is configured

6. Update the README with usage examples for category-based routing.

## Notes

This should be implemented as an incremental enhancement rather than a breaking change. The default behavior should remain spam-only unless the user enables category routing explicitly.
