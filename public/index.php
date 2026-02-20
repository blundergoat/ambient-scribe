<?php

/**
 * Symfony front controller - the entry point for ALL HTTP requests.
 *
 * Every request hits this file first (Apache/Nginx/PHP built-in server routes here).
 * It boots the Symfony runtime, which creates the Kernel, handles the request,
 * and sends the response.
 *
 * HOW IT WORKS:
 *   1. autoload_runtime.php loads Composer's autoloader AND the Symfony Runtime component
 *   2. The closure returns a Kernel instance configured with the current environment
 *   3. The Runtime component calls Kernel->handle($request) and sends the response
 *
 * ENVIRONMENT VARIABLES:
 *   APP_ENV   - "dev" (debug mode, detailed errors) or "prod" (optimized, no debug)
 *   APP_DEBUG - "1" to enable the Symfony debug toolbar, "0" to disable
 *
 * You should almost never need to modify this file.
 */

use App\Kernel;

require_once dirname(__DIR__).'/vendor/autoload_runtime.php';

return function (array $context) {
    return new Kernel($context['APP_ENV'], (bool) $context['APP_DEBUG']);
};
