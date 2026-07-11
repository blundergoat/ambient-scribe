<?php

/**
 * Route isolated browser tests through PHP's built-in server.
 *
 * Existing files under `public/` are served as browser assets. Dynamic URLs
 * enter Symfony through `public/index.php`, matching the local app without
 * letting parallel workers execute JavaScript files as PHP entrypoints.
 */

declare(strict_types=1);

$publicRoot = dirname(__DIR__).'/public';
$requestPath = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
// A malformed URL still belongs to Symfony, which can return the user-facing error.
if (!is_string($requestPath)) {
    $requestPath = '/';
}

$requestedFile = realpath($publicRoot.$requestPath);
$publicFilePrefix = $publicRoot.DIRECTORY_SEPARATOR;
// Only a real file inside public may bypass Symfony and render as a browser asset.
if (
    false !== $requestedFile
    && str_starts_with($requestedFile, $publicFilePrefix)
    && is_file($requestedFile)
) {
    return false;
}

require $publicRoot.'/index.php';
